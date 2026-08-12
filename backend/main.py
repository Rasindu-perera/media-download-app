"""
Media Downloader API — FastAPI backend

Download architecture (triple-redundant):
  1. Piped API      — alternative YT frontend, works from any IP
  2. Invidious API  — another YT frontend, proxy streams via /latest_version
  3. Cobalt API     — general media downloader (YouTube + other platforms)

Metadata architecture:
  • Video title: YouTube oembed (official Google, always works) → Piped → Invidious
  • Playlists:   Piped → Invidious
  • Spotify:     scrape embed → youtubesearchpython → Piped search fallback

Instance lists are fetched dynamically at startup from official registries
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
    "2160p": "2160", "2160": "2160", "4k": "2160",
    "1440p": "1440", "1440": "1440", "1080p": "1080", "1080": "1080",
    "720p": "720", "720": "720", "480p": "480", "480": "480",
    "360p": "360", "360": "360", "240p": "240", "240": "240",
}

AUDIO_BITRATE_MAP: Dict[str, str] = {
    "320k": "320", "320": "320", "256k": "256", "256": "256",
    "128k": "128", "128": "128", "96k": "96", "96": "96",
}

VALID_AUDIO_FORMATS = {"mp3", "ogg", "wav", "opus", "best"}

# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _is_youtube_url(url: str) -> bool:
    u = url.lower()
    return "youtube.com" in u or "youtu.be" in u

def _extract_yt_video_id(url: str) -> Optional[str]:
    for p in [
        r'(?:youtube\.com/watch\?.*?v=)([a-zA-Z0-9_-]{11})',
        r'(?:youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/shorts/)([a-zA-Z0-9_-]{11})',
    ]:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None

def _extract_yt_playlist_id(url: str) -> Optional[str]:
    m = re.search(r'[?&]list=([a-zA-Z0-9_-]+)', url)
    return m.group(1) if m else None


# ===================================================================
# YOUTUBE OEMBED — always-available video title (official Google API)
# ===================================================================

def _yt_oembed_info(video_id: str) -> dict:
    """
    Fetch video title + thumbnail via YouTube's official oembed endpoint.
    This is Google infrastructure — works from ANY IP, no auth, never blocked.
    """
    oembed_url = (
        f"https://www.youtube.com/oembed"
        f"?url=https://www.youtube.com/watch?v={video_id}&format=json"
    )
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(oembed_url)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "title":     data.get("title", "YouTube Video"),
                    "author":    data.get("author_name", ""),
                    "thumbnail": (
                        data.get("thumbnail_url")
                        or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                    ),
                }
    except Exception as e:
        print(f"[oembed] Failed for {video_id}: {e}")

    # Absolute fallback — thumbnail URL always works
    return {
        "title":     "YouTube Video",
        "author":    "",
        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
    }


# ===================================================================
# PYTUBEFIX — YouTube extraction (requires PO_TOKEN)
# ===================================================================

import json
from pytubefix import YouTube, Playlist

def _init_pytubefix(url: str, is_playlist: bool = False):
    try:
        # Use TV client to bypass BotGuard without needing PO Tokens
        if is_playlist:
            return Playlist(url, client='TV')
        return YouTube(url, client='TV')
    except Exception as e:
        raise Exception(f"pytubefix initialization failed: {e}")

def _pytubefix_get_playlist(playlist_id: str) -> dict:
    url = f"https://www.youtube.com/playlist?list={playlist_id}"
    pl = _init_pytubefix(url, is_playlist=True)
    
    items = []
    try:
        for v in pl.videos:
            items.append({
                "id": v.watch_url,
                "title": v.title,
                "thumbnail": v.thumbnail_url,
                "duration": v.length,
                "artist": v.author,
            })
    except Exception as e:
        raise Exception(f"Failed to fetch pytubefix playlist tracks: {e}")

    if not items:
        raise Exception("pytubefix returned an empty playlist.")

    return {
        "type": "playlist",
        "playlist_title": pl.title if pl.title else "YouTube Playlist",
        "items": items,
        "count": len(items),
        "platform": "youtube",
    }

def _pytubefix_get_stream_url(video_id: str, format_type: str, quality: str) -> Tuple[str, str]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    yt = _init_pytubefix(url, is_playlist=False)

    try:
        if format_type == "audio":
            stream = yt.streams.get_audio_only()
            if not stream:
                raise Exception("No audio streams found by pytubefix.")
            ext = stream.subtype if stream.subtype else "mp4"
            # Return raw stream URL
            return stream.url, ext
            
        else:
            # Video
            target_res = quality.replace("p", "") + "p"
            stream = yt.streams.filter(res=target_res, progressive=True).first()
            if not stream:
                stream = yt.streams.get_highest_resolution()
            if not stream:
                raise Exception("No video streams found by pytubefix.")
            ext = stream.subtype if stream.subtype else "mp4"
            # Return raw stream URL
            return stream.url, ext
    except Exception as e:
        raise Exception(f"pytubefix stream extraction failed: {e}")

def _pytubefix_search(query: str) -> Optional[str]:
    # pytubefix search is not as reliable, rely on youtubesearchpython first
    from pytubefix import Search
    try:
        s = Search(query)
        if s.results:
            return s.results[0].watch_url
    except Exception:
        pass
    return None


# ===================================================================
# COBALT API — for non-YouTube platforms + YouTube last-resort fallback
# ===================================================================

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
    global COBALT_INSTANCES
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get("https://cobalt.directory/api/working?type=api")
            if resp.status_code == 200:
                data = resp.json()
                urls = []
                for inst in data:
                    api_url = inst.get("api_url") or inst.get("url", "")
                    if not inst.get("turnstile", False) and api_url:
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
                    print(f"[startup] Loaded {len(merged)} Cobalt instances")
                    return
    except Exception as e:
        print(f"[startup] Failed to fetch Cobalt instances: {e}")
    print(f"[startup] Using {len(COBALT_INSTANCES)} hardcoded Cobalt instances")


def _fetch_session_token(inst: str) -> Optional[str]:
    cached = _token_cache.get(inst)
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(
                inst.rstrip("/") + "/session",
                headers=COBALT_HEADERS, json={},
            )
        if resp.status_code == 200:
            token = resp.json().get("token")
            if token and isinstance(token, str):
                _token_cache[inst] = (token, time.time() + 50 * 60)
                return token
    except Exception:
        pass
    return None


def _call_cobalt(payload: dict) -> dict:
    last_error = "No Cobalt instances."
    with httpx.Client(timeout=45.0) as client:
        for inst in COBALT_INSTANCES:
            try:
                token = _fetch_session_token(inst)
                headers = dict(COBALT_HEADERS)
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                resp = client.post(inst, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    ec = ""
                    if isinstance(data.get("error"), dict):
                        ec = data["error"].get("code", "")
                    if "auth" in ec:
                        last_error = f"{inst}: {ec}"
                        continue
                    return data
                if resp.status_code in (401, 403):
                    last_error = f"{inst}: HTTP {resp.status_code}"
                    continue
                try:
                    eb = resp.json()
                except Exception:
                    eb = resp.text
                last_error = f"{inst}: HTTP {resp.status_code} — {eb}"
            except Exception as e:
                last_error = f"{inst}: {e}"
    raise Exception(f"All Cobalt instances failed. Last: {last_error}")


def _build_cobalt_payload(url: str, fmt_type: str, quality: str, fmt: str) -> dict:
    p: dict = {"url": url}
    if fmt_type == "audio":
        p["downloadMode"] = "audio"
        p["audioFormat"] = fmt.lower() if fmt.lower() in VALID_AUDIO_FORMATS else "mp3"
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


# ===================================================================
# Composite helpers
# ===================================================================

def _search_youtube(query: str) -> str:
    """Search YouTube: youtubesearchpython first, then pytubefix fallback."""
    try:
        from youtubesearchpython import VideosSearch
        results = VideosSearch(query, limit=1).result()
        if results and results.get("result"):
            return results["result"][0]["link"]
    except Exception as e:
        print(f"[search] youtubesearchpython failed: {e}")

    url = _pytubefix_search(query)
    if url:
        return url
    raise Exception(f"No YouTube results for: {query}")


def _get_yt_download_url(
    video_id: str, format_type: str, quality: str, file_format: str,
    original_url: str,
) -> Tuple[str, str]:
    """
    Fallback YouTube download: pytubefix → Cobalt.
    Returns (stream_url, file_extension).
    """
    errors: List[str] = []

    # --- 1. pytubefix ---
    try:
        url, ext = _pytubefix_get_stream_url(video_id, format_type, quality)
        print(f"[download] pytubefix succeeded for {video_id}")
        return url, ext
    except Exception as e:
        errors.append(f"pytubefix: {e}")
        print(f"[download] pytubefix failed for {video_id}: {e}")

    # --- 2. Cobalt ---
    try:
        payload = _build_cobalt_payload(
            original_url, format_type, quality, file_format
        )
        data = _call_cobalt(payload)
        dl_url = _extract_cobalt_url(data)
        print(f"[download] Cobalt succeeded for {video_id}")
        return dl_url, file_format
    except Exception as e:
        errors.append(f"Cobalt: {e}")
        print(f"[download] Cobalt failed for {video_id}: {e}")

    raise Exception(
        f"All download methods failed for {video_id}:\n"
        + "\n".join(errors)
    )


# ===================================================================
# Startup
# ===================================================================

@app.on_event("startup")
async def on_startup():
    await asyncio.to_thread(_refresh_cobalt_instances)


# ===================================================================
# Routes
# ===================================================================

@app.get("/")
async def health_check():
    return {
        "status": "ok",
        "pytubefix": "active",
        "cobalt": len(COBALT_INSTANCES),
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
    try:
        target_url = request.url

        # --- Spotify → find on YouTube first ---
        if is_spotify_url(request.url):
            if request.is_playlist:
                raise HTTPException(
                    status_code=400, detail="Download tracks individually."
                )
            from spotify_api import get_spotify_content_info
            info = get_spotify_content_info(request.url)
            if info.get("type") != "single":
                raise Exception("Not a single Spotify track.")
            track = info["data"]
            query = f"{track.get('artist', '')} {track.get('title', '')}".strip()
            target_url = await asyncio.to_thread(_search_youtube, query)

        # --- YouTube (original or Spotify-resolved) ---
        if _is_youtube_url(target_url):
            video_id = _extract_yt_video_id(target_url)
            if not video_id:
                raise Exception(f"Cannot extract video ID from: {target_url}")

            stream_url, ext = await asyncio.to_thread(
                _get_yt_download_url,
                video_id,
                request.format_type,
                request.quality,
                request.file_format,
                target_url,
            )
            return {"task_id": "stream", "url": stream_url, "actual_format": ext}

        # --- Other platforms → Cobalt directly ---
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

        # --- YouTube playlist (pytubefix) ---
        pl_id = _extract_yt_playlist_id(url)
        if pl_id:
            try:
                result = await asyncio.to_thread(_pytubefix_get_playlist, pl_id)
                if result.get("items"):
                    return result
            except Exception as e:
                print(f"[playlist-info] pytubefix playlist error: {e}")

            # Return empty playlist with title rather than falling through
            return {
                "type": "playlist",
                "playlist_title": "YouTube Playlist",
                "items": [],
                "count": 0,
                "platform": "youtube",
            }

        # --- Single YouTube video → oembed (always works) ---
        vid_id = _extract_yt_video_id(url)
        if vid_id:
            info = await asyncio.to_thread(_yt_oembed_info, vid_id)
            return {
                "type": "single",
                "data": {
                    "title":     info["title"],
                    "artist":    info["author"],
                    "thumbnail": info["thumbnail"],
                },
                "platform": "youtube",
            }

        # Fallback for unknown URLs
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
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)