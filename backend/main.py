"""
Media Downloader API — FastAPI backend

Strategy:
  • YouTube: Piped API (video info + stream URLs) with Cobalt as fallback
  • Spotify: scrape embed → YouTube search → Piped/Cobalt stream
  • Other platforms: Cobalt API
  • Instance lists: fetched dynamically from cobalt.directory at startup,
    with hardcoded fallbacks for resilience.
"""

import asyncio
import os
import re
import time
import urllib.parse
from typing import AsyncIterator, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from spotify_handler import get_spotify_details, is_spotify_url

app = FastAPI(title="Media Downloader API")

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class DownloadRequest(BaseModel):
    url: str
    format_type: str
    quality: str
    file_format: str
    is_playlist: bool = False
    selected_indices: Optional[List[int]] = None

class PlaylistRequest(BaseModel):
    url: str

# ---------------------------------------------------------------------------
# Quality maps
# ---------------------------------------------------------------------------

VIDEO_QUALITY_MAP: Dict[str, str] = {
    "max": "max", "4320p": "4320", "4320": "4320",
    "2160p": "2160", "2160": "2160", "4k": "2160", "4K": "2160",
    "1440p": "1440", "1440": "1440", "1080p": "1080", "1080": "1080",
    "720p": "720", "720": "720", "480p": "480", "480": "480",
    "360p": "360", "360": "360", "240p": "240", "240": "240",
    "144p": "144", "144": "144",
}

AUDIO_BITRATE_MAP: Dict[str, str] = {
    "320k": "320", "320": "320", "256k": "256", "256": "256",
    "128k": "128", "128": "128", "96k": "96", "96": "96",
    "64k": "64", "64": "64",
}

VALID_AUDIO_FORMATS = {"mp3", "ogg", "wav", "opus", "best"}

# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _is_youtube_url(url: str) -> bool:
    u = url.lower()
    return "youtube.com" in u or "youtu.be" in u

def _extract_yt_video_id(url: str) -> Optional[str]:
    for pat in [
        r'(?:youtube\.com/watch\?.*?v=)([a-zA-Z0-9_-]{11})',
        r'(?:youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/shorts/)([a-zA-Z0-9_-]{11})',
    ]:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None

def _extract_yt_playlist_id(url: str) -> Optional[str]:
    m = re.search(r'[?&]list=([a-zA-Z0-9_-]+)', url)
    return m.group(1) if m else None

# ===========================================================================
# PIPED API — for YouTube video info, stream URLs, playlists, and search
# ===========================================================================

# Hardcoded instances (verified working 2026-08-12) + dynamic fetch at startup
PIPED_INSTANCES: List[str] = [
    "https://api.piped.private.coffee",
]

def _refresh_piped_instances() -> None:
    """
    Fetch the live Piped instance list from the official registry.
    Merges with the hardcoded fallbacks. Called once at startup.
    """
    global PIPED_INSTANCES
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get("https://piped-instances.kavin.rocks/")
            if resp.status_code == 200:
                data = resp.json()
                urls = []
                for inst in data:
                    api_url = inst.get("api_url", "")
                    # Only include instances with decent uptime
                    uptime = inst.get("uptime_24h", 0)
                    if api_url and uptime > 50:
                        urls.append(api_url.rstrip("/"))
                if urls:
                    # Merge: dynamic first, then hardcoded fallbacks
                    seen = set()
                    merged = []
                    for u in urls + PIPED_INSTANCES:
                        if u not in seen:
                            seen.add(u)
                            merged.append(u)
                    PIPED_INSTANCES = merged
                    print(f"[startup] Loaded {len(merged)} Piped instances")
                    return
    except Exception as e:
        print(f"[startup] Failed to fetch Piped instances: {e}")
    print(f"[startup] Using {len(PIPED_INSTANCES)} hardcoded Piped instances")


def _piped_request(path: str) -> dict:
    """GET a Piped API endpoint, trying each instance until one succeeds."""
    last_error = "No Piped instances."
    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        for inst in PIPED_INSTANCES:
            try:
                resp = client.get(f"{inst}{path}")
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict) and data.get("error"):
                        last_error = f"{inst}: {data['error']}"
                        continue
                    return data
                last_error = f"{inst}: HTTP {resp.status_code}"
            except httpx.TimeoutException:
                last_error = f"{inst}: timeout"
            except httpx.ConnectError:
                last_error = f"{inst}: unreachable"
            except Exception as e:
                last_error = f"{inst}: {e}"
    raise Exception(f"All Piped instances failed. Last: {last_error}")


def _piped_get_video_info(video_id: str) -> dict:
    data = _piped_request(f"/streams/{video_id}")
    return {
        "title": data.get("title") or "Unknown",
        "author": data.get("uploader") or "",
        "thumbnail": data.get("thumbnailUrl"),
        "duration": data.get("duration", 0),
    }


def _piped_get_stream_url(
    video_id: str, format_type: str, quality: str
) -> Tuple[str, str]:
    """Get a direct stream URL from Piped. Returns (url, file_ext)."""
    data = _piped_request(f"/streams/{video_id}")

    if format_type == "audio":
        streams = data.get("audioStreams") or []
        if not streams:
            raise Exception("No audio streams available.")
        streams.sort(key=lambda s: s.get("bitrate", 0), reverse=True)
        s = streams[0]
        mime = s.get("mimeType", "")
        ext = "m4a" if "mp4" in mime else "webm"
        return s["url"], ext

    # Video — prefer muxed (videoOnly == false)
    all_vs = data.get("videoStreams") or []
    muxed = [s for s in all_vs if not s.get("videoOnly", True)]
    target = quality.replace("p", "") + "p"

    for s in muxed:
        if s.get("quality") == target:
            ext = "mp4" if "mp4" in s.get("mimeType", "") else "webm"
            return s["url"], ext

    if muxed:
        muxed.sort(
            key=lambda s: int(re.sub(r'\D', '', s.get("quality", "0")) or 0),
            reverse=True,
        )
        s = muxed[0]
        ext = "mp4" if "mp4" in s.get("mimeType", "") else "webm"
        return s["url"], ext

    if all_vs:
        all_vs.sort(key=lambda s: s.get("bitrate", 0), reverse=True)
        s = all_vs[0]
        ext = "mp4" if "mp4" in s.get("mimeType", "") else "webm"
        return s["url"], ext

    raise Exception("No video streams available.")


def _piped_get_playlist(playlist_id: str) -> dict:
    data = _piped_request(f"/playlists/{playlist_id}")
    all_items = list(data.get("relatedStreams") or [])
    nextpage = data.get("nextpage")
    pages = 0
    while nextpage and pages < 10:
        enc = urllib.parse.quote(str(nextpage), safe="")
        pg = _piped_request(f"/nextpage/playlists/{playlist_id}?nextpage={enc}")
        all_items.extend(pg.get("relatedStreams") or [])
        nextpage = pg.get("nextpage")
        pages += 1

    items = []
    for s in all_items:
        vid_url = s.get("url", "")
        m = re.search(r'v=([a-zA-Z0-9_-]{11})', vid_url)
        vid_id = m.group(1) if m else ""
        items.append({
            "id": f"https://www.youtube.com/watch?v={vid_id}" if vid_id else "",
            "title": s.get("title") or "Unknown",
            "thumbnail": s.get("thumbnail"),
            "duration": s.get("duration"),
            "artist": s.get("uploaderName") or "",
        })
    return {
        "type": "playlist",
        "playlist_title": data.get("name") or "YouTube Playlist",
        "items": items,
        "count": len(items),
        "platform": "youtube",
    }


def _piped_search(query: str) -> Optional[str]:
    """Search YouTube via Piped. Returns full watch URL or None."""
    try:
        enc = urllib.parse.quote(query, safe="")
        data = _piped_request(f"/search?q={enc}&filter=videos")
        items = data.get("items") or data.get("relatedStreams") or []
        if items:
            rel = items[0].get("url", "")
            if rel.startswith("/watch"):
                return f"https://www.youtube.com{rel}"
    except Exception:
        pass
    return None


def _search_youtube(query: str) -> str:
    """Search YouTube: youtubesearchpython first, Piped fallback."""
    try:
        from youtubesearchpython import VideosSearch
        results = VideosSearch(query, limit=1).result()
        if results and results.get("result"):
            return results["result"][0]["link"]
    except Exception as e:
        print(f"[search] youtubesearchpython failed: {e}")

    url = _piped_search(query)
    if url:
        return url
    raise Exception(f"No YouTube results for: {query}")


# ===========================================================================
# COBALT API — for non-YouTube platforms AND as YouTube download fallback
# ===========================================================================

# Hardcoded instances (verified 2026-08-12) + dynamic fetch at startup
COBALT_INSTANCES: List[str] = [
    "https://rue-cobalt.xenon.zone/",
    "https://cobaltapi.cjs.nz/",
]

COBALT_HEADERS: Dict[str, str] = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}
_token_cache: Dict[str, Tuple[str, float]] = {}


def _refresh_cobalt_instances() -> None:
    """
    Fetch the live Cobalt instance list from cobalt.directory.
    Only keeps instances that are UP and do NOT require Turnstile.
    """
    global COBALT_INSTANCES
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get("https://cobalt.directory/api/working?type=api")
            if resp.status_code == 200:
                data = resp.json()
                urls = []
                for inst in data:
                    api_url = inst.get("api_url") or inst.get("url", "")
                    turnstile = inst.get("turnstile", False)
                    if api_url and not turnstile:
                        if not api_url.startswith("http"):
                            api_url = f"https://{api_url}"
                        if not api_url.endswith("/"):
                            api_url += "/"
                        urls.append(api_url)
                if urls:
                    seen = set()
                    merged = []
                    for u in urls + COBALT_INSTANCES:
                        if u not in seen:
                            seen.add(u)
                            merged.append(u)
                    COBALT_INSTANCES = merged
                    print(f"[startup] Loaded {len(merged)} Cobalt instances (no-auth)")
                    return
    except Exception as e:
        print(f"[startup] Failed to fetch Cobalt instances: {e}")
    print(f"[startup] Using {len(COBALT_INSTANCES)} hardcoded Cobalt instances")


def _fetch_session_token(instance_url: str) -> Optional[str]:
    cached = _token_cache.get(instance_url)
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(
                instance_url.rstrip("/") + "/session",
                headers=COBALT_HEADERS, json={},
            )
        if resp.status_code == 200:
            token = resp.json().get("token")
            if token and isinstance(token, str):
                _token_cache[instance_url] = (token, time.time() + 50 * 60)
                return token
    except Exception:
        pass
    return None


def _call_cobalt(payload: dict) -> dict:
    """Try each Cobalt instance with optional session token."""
    last_error = "No Cobalt instances."
    with httpx.Client(timeout=45.0) as client:
        for inst_url in COBALT_INSTANCES:
            try:
                token = _fetch_session_token(inst_url)
                headers = dict(COBALT_HEADERS)
                if token:
                    headers["Authorization"] = f"Bearer {token}"

                resp = client.post(inst_url, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    err_code = ""
                    if isinstance(data.get("error"), dict):
                        err_code = data["error"].get("code", "")
                    if "auth" in err_code:
                        last_error = f"{inst_url}: {err_code}"
                        continue
                    return data
                if resp.status_code in (401, 403):
                    last_error = f"{inst_url}: HTTP {resp.status_code}"
                    continue
                try:
                    eb = resp.json()
                except Exception:
                    eb = resp.text
                last_error = f"{inst_url}: HTTP {resp.status_code} — {eb}"
            except httpx.TimeoutException:
                last_error = f"{inst_url}: timeout"
            except httpx.ConnectError:
                last_error = f"{inst_url}: unreachable"
            except Exception as e:
                last_error = f"{inst_url}: {e}"
    raise Exception(f"All Cobalt instances failed. Last: {last_error}")


def _build_cobalt_payload(
    url: str, format_type: str, quality: str, file_format: str
) -> dict:
    p: dict = {"url": url}
    if format_type == "audio":
        p["downloadMode"] = "audio"
        p["audioFormat"] = file_format.lower() if file_format.lower() in VALID_AUDIO_FORMATS else "mp3"
        p["audioBitrate"] = AUDIO_BITRATE_MAP.get(quality, "128")
    else:
        p["downloadMode"] = "auto"
        p["videoQuality"] = VIDEO_QUALITY_MAP.get(quality, "1080")
    return p


def _extract_cobalt_url(data: dict) -> str:
    if "url" in data:
        return data["url"]
    if data.get("status") == "picker" and data.get("picker"):
        return data["picker"][0].get("url", "")
    raise Exception(f"Unexpected Cobalt response: {data}")


# ===========================================================================
# Startup — refresh instance lists
# ===========================================================================

@app.on_event("startup")
async def on_startup():
    """Fetch live instance lists on server boot (non-blocking)."""
    await asyncio.to_thread(_refresh_piped_instances)
    await asyncio.to_thread(_refresh_cobalt_instances)


# ===========================================================================
# Routes
# ===========================================================================

@app.get("/")
async def health_check():
    return {
        "status": "ok",
        "message": "Media Downloader API is running",
        "piped_instances": len(PIPED_INSTANCES),
        "cobalt_instances": len(COBALT_INSTANCES),
    }


@app.post("/api/formats")
async def get_formats(request: Request):
    return {
        "status": "success",
        "video": ["max", "1080", "720", "480"],
        "audio": ["320k", "256k", "128k"],
    }


@app.get("/api/proxy")
async def proxy_download(
    url: str = Query(...),
    filename: str = Query(default="download"),
):
    """
    Stream a remote URL through our server with Content-Disposition: attachment.
    Makes downloads same-origin → browser saves to disk + shows multi-download prompt.
    """
    safe_name = urllib.parse.quote(filename, safe="")

    async def _stream() -> AsyncIterator[bytes]:
        timeout = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                async for chunk in resp.aiter_bytes(chunk_size=65_536):
                    yield chunk

    return StreamingResponse(
        _stream(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{safe_name}",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache",
        },
    )


@app.post("/api/download")
async def start_download(request: DownloadRequest):
    """
    Get a download URL for the requested media.
    YouTube: tries Piped first, then Cobalt as fallback.
    Spotify: resolves to YouTube first.
    Other: Cobalt directly.
    """
    try:
        target_url = request.url

        # --- Spotify → YouTube ---
        if is_spotify_url(request.url):
            if request.is_playlist:
                raise HTTPException(status_code=400, detail="Download tracks individually.")

            from spotify_api import get_spotify_content_info
            info = get_spotify_content_info(request.url)
            if info.get("type") != "single":
                raise Exception("Not a single track.")
            track = info["data"]
            query = f"{track.get('artist', '')} {track.get('title', '')}".strip()
            target_url = await asyncio.to_thread(_search_youtube, query)

        # --- YouTube → Piped, then Cobalt fallback ---
        if _is_youtube_url(target_url):
            video_id = _extract_yt_video_id(target_url)
            if not video_id:
                raise Exception(f"Cannot extract video ID from: {target_url}")

            # Try Piped first
            try:
                stream_url, ext = await asyncio.to_thread(
                    _piped_get_stream_url, video_id,
                    request.format_type, request.quality,
                )
                return {"task_id": "piped", "url": stream_url, "actual_format": ext}
            except Exception as piped_err:
                print(f"[download] Piped failed: {piped_err}, trying Cobalt...")

            # Fallback to Cobalt for YouTube
            try:
                payload = _build_cobalt_payload(
                    target_url, request.format_type,
                    request.quality, request.file_format,
                )
                data = _call_cobalt(payload)
                dl_url = _extract_cobalt_url(data)
                return {"task_id": "cobalt", "url": dl_url, "actual_format": request.file_format}
            except Exception as cobalt_err:
                raise Exception(
                    f"Both Piped and Cobalt failed.\n"
                    f"Piped: {piped_err}\n"
                    f"Cobalt: {cobalt_err}"
                )

        # --- Other platforms → Cobalt ---
        payload = _build_cobalt_payload(
            target_url, request.format_type, request.quality, request.file_format,
        )
        data = _call_cobalt(payload)
        dl_url = _extract_cobalt_url(data)
        return {"task_id": "cobalt", "url": dl_url, "actual_format": request.file_format}

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/playlist-info")
async def get_playlist_info(request: PlaylistRequest):
    """
    Return playlist / track metadata.
    Spotify: scrape embed, return Spotify track URLs.
    YouTube playlist: Piped API.
    YouTube single: Piped API for real title.
    """
    try:
        url = request.url

        # --- Spotify ---
        if is_spotify_url(url):
            pinfo = await get_spotify_details(url)
            if pinfo.get("type") == "single":
                pinfo["platform"] = "spotify"
                return pinfo
            for item in pinfo.get("items", []):
                tid = item.get("id", "")
                if tid and not tid.startswith("http"):
                    item["id"] = f"https://open.spotify.com/track/{tid}"
            pinfo["platform"] = "spotify"
            if "type" not in pinfo:
                pinfo["type"] = "playlist"
            return pinfo

        # --- YouTube playlist ---
        pl_id = _extract_yt_playlist_id(url)
        if pl_id:
            try:
                return await asyncio.to_thread(_piped_get_playlist, pl_id)
            except Exception as e:
                print(f"[playlist-info] Piped playlist error: {e}")

        # --- YouTube single video ---
        vid_id = _extract_yt_video_id(url)
        if vid_id:
            try:
                info = await asyncio.to_thread(_piped_get_video_info, vid_id)
                return {
                    "type": "single",
                    "data": {
                        "title": info["title"],
                        "artist": info["author"],
                        "thumbnail": info["thumbnail"],
                    },
                    "platform": "youtube",
                }
            except Exception as e:
                print(f"[playlist-info] Piped video info error: {e}")

        return {
            "type": "single",
            "data": {"title": "Media", "artist": "", "thumbnail": None},
            "platform": "other",
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)