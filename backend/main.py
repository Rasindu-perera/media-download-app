"""
Media Downloader API — FastAPI backend

Download architecture:
  • YouTube (single & playlist): Piped API
    → Alternative YouTube frontend with a REST API; works from any IP.
    → Provides video info (title, thumbnail) and direct stream URLs.
    → Multiple public instances used as fallbacks.
  • Spotify: scrape embed metadata → search YouTube via youtubesearchpython
    → resolve to YouTube → Piped stream URL.
  • Other platforms (Instagram, TikTok, etc.): Cobalt API fallback chain.

  /api/proxy streams any remote URL through the server with proper
  Content-Disposition headers so the browser saves files to disk and shows
  the "allow multiple downloads" prompt for playlists.
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
    format_type: str           # 'video' or 'audio'
    quality: str               # e.g. "720p", "1080p", "128k", "320k"
    file_format: str           # mp4/webm for video; mp3/m4a/opus for audio
    is_playlist: bool = False
    selected_indices: Optional[List[int]] = None

class PlaylistRequest(BaseModel):
    url: str

# ---------------------------------------------------------------------------
# Quality maps
# ---------------------------------------------------------------------------

VIDEO_QUALITY_MAP: Dict[str, str] = {
    "max":   "max",
    "4320p": "4320", "4320": "4320",
    "2160p": "2160", "2160": "2160", "4k": "2160", "4K": "2160",
    "1440p": "1440", "1440": "1440",
    "1080p": "1080", "1080": "1080",
    "720p":  "720",  "720":  "720",
    "480p":  "480",  "480":  "480",
    "360p":  "360",  "360":  "360",
    "240p":  "240",  "240":  "240",
    "144p":  "144",  "144":  "144",
}

AUDIO_BITRATE_MAP: Dict[str, str] = {
    "320k": "320", "320": "320",
    "256k": "256", "256": "256",
    "128k": "128", "128": "128",
    "96k":  "96",  "96":  "96",
    "64k":  "64",  "64":  "64",
}

VALID_AUDIO_FORMATS = {"mp3", "ogg", "wav", "opus", "best"}

# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _is_youtube_url(url: str) -> bool:
    url_l = url.lower()
    return "youtube.com" in url_l or "youtu.be" in url_l


def _extract_yt_video_id(url: str) -> Optional[str]:
    """Extract the 11-char YouTube video ID from any YouTube URL variant."""
    patterns = [
        r'(?:youtube\.com/watch\?.*?v=)([a-zA-Z0-9_-]{11})',
        r'(?:youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/v/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/shorts/)([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def _extract_yt_playlist_id(url: str) -> Optional[str]:
    """Extract the playlist ID (list= parameter) from a YouTube URL."""
    m = re.search(r'[?&]list=([a-zA-Z0-9_-]+)', url)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Piped API — Alternative YouTube frontend REST API
#
# Endpoints used:
#   GET /streams/{videoId}                     → video info + stream URLs
#   GET /playlists/{playlistId}                → playlist info + first page
#   GET /nextpage/playlists/{id}?nextpage=...  → playlist pagination
#   GET /search?q=...&filter=videos            → search
#
# Multiple public instances are tried in order as fallbacks.
# ---------------------------------------------------------------------------

PIPED_INSTANCES: List[str] = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://api.piped.projectsegfau.lt",
    "https://pipedapi.r4fo.com",
    "https://pipedapi.in.projectsegfau.lt",
]


def _piped_request(path: str) -> dict:
    """
    Make a GET request to the Piped API, trying each instance until one
    succeeds. Returns the parsed JSON on success. Raises Exception if all fail.
    """
    last_error = "No Piped instances configured."

    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        for instance in PIPED_INSTANCES:
            try:
                url = f"{instance}{path}"
                resp = client.get(url)

                if resp.status_code == 200:
                    data = resp.json()
                    # Some instances return {"error": "..."} with 200 status
                    if isinstance(data, dict) and data.get("error"):
                        last_error = f"{instance}: {data['error']}"
                        continue
                    return data

                last_error = f"{instance}: HTTP {resp.status_code}"

            except httpx.TimeoutException:
                last_error = f"{instance}: timed out"
            except httpx.ConnectError:
                last_error = f"{instance}: unreachable"
            except Exception as exc:
                last_error = f"{instance}: {exc}"

    raise Exception(f"All Piped instances failed. Last error: {last_error}")


def _piped_get_video_info(video_id: str) -> dict:
    """
    Fetch title, uploader, thumbnail, and duration for a YouTube video
    using the Piped /streams endpoint.
    """
    data = _piped_request(f"/streams/{video_id}")
    return {
        "title":     data.get("title") or "Unknown",
        "author":    data.get("uploader") or "",
        "thumbnail": data.get("thumbnailUrl"),
        "duration":  data.get("duration", 0),
    }


def _piped_get_stream_url(
    video_id: str, format_type: str, quality: str
) -> Tuple[str, str]:
    """
    Get a direct stream URL from Piped for a YouTube video.

    Returns (stream_url, file_extension).

    Audio: picks the highest-bitrate audio stream (typically M4A).
    Video: picks a muxed (video+audio) stream at the requested quality,
           falling back to the highest available muxed stream.
           Muxed streams cap at 720p on YouTube; higher needs server-side
           merging which is not supported in this architecture.
    """
    data = _piped_request(f"/streams/{video_id}")

    # --- Audio ---
    if format_type == "audio":
        audio_streams = data.get("audioStreams") or []
        if not audio_streams:
            raise Exception("No audio streams available for this video.")

        # Sort by bitrate descending, pick the best one
        audio_streams.sort(key=lambda s: s.get("bitrate", 0), reverse=True)
        stream = audio_streams[0]

        mime = stream.get("mimeType", "audio/mp4")
        if "mp4" in mime:
            ext = "m4a"
        elif "webm" in mime or "opus" in mime:
            ext = "webm"
        else:
            ext = "m4a"

        return stream["url"], ext

    # --- Video ---
    video_streams = data.get("videoStreams") or []

    # Prefer muxed streams (videoOnly == false) — these include audio
    muxed = [s for s in video_streams if not s.get("videoOnly", True)]

    # Normalise quality target: "720p" / "720" → "720p"
    quality_num = quality.replace("p", "")
    target = f"{quality_num}p"

    # Try exact quality match in muxed streams
    for s in muxed:
        if s.get("quality") == target:
            ext = "mp4" if "mp4" in s.get("mimeType", "") else "webm"
            return s["url"], ext

    # Fallback: highest-resolution muxed stream
    if muxed:
        def _res(s):
            q = s.get("quality", "0p").replace("p", "")
            try:
                return int(q)
            except ValueError:
                return 0
        muxed.sort(key=_res, reverse=True)
        stream = muxed[0]
        ext = "mp4" if "mp4" in stream.get("mimeType", "") else "webm"
        return stream["url"], ext

    # No muxed streams at all — return best adaptive video-only stream
    # (will have no audio, but better than nothing)
    if video_streams:
        video_streams.sort(key=lambda s: s.get("bitrate", 0), reverse=True)
        stream = video_streams[0]
        ext = "mp4" if "mp4" in stream.get("mimeType", "") else "webm"
        return stream["url"], ext

    raise Exception("No video streams available for this video.")


def _piped_get_playlist(playlist_id: str) -> dict:
    """
    Fetch YouTube playlist metadata from Piped, including pagination
    for large playlists (up to ~500 items / 10 pages).
    """
    data = _piped_request(f"/playlists/{playlist_id}")

    all_streams: list = list(data.get("relatedStreams") or [])
    nextpage = data.get("nextpage")
    pages = 0

    while nextpage and pages < 10:
        encoded = urllib.parse.quote(str(nextpage), safe="")
        page_data = _piped_request(
            f"/nextpage/playlists/{playlist_id}?nextpage={encoded}"
        )
        all_streams.extend(page_data.get("relatedStreams") or [])
        nextpage = page_data.get("nextpage")
        pages += 1

    items: List[dict] = []
    for stream in all_streams:
        # stream["url"] is relative: "/watch?v=xxxxx"
        video_url = stream.get("url", "")
        vid_match = re.search(r'v=([a-zA-Z0-9_-]{11})', video_url)
        vid_id = vid_match.group(1) if vid_match else ""
        watch_url = (
            f"https://www.youtube.com/watch?v={vid_id}" if vid_id else ""
        )

        items.append({
            "id":        watch_url,
            "title":     stream.get("title") or "Unknown",
            "thumbnail": stream.get("thumbnail"),
            "duration":  stream.get("duration"),
            "artist":    stream.get("uploaderName") or "",
        })

    return {
        "type":           "playlist",
        "playlist_title": data.get("name") or "YouTube Playlist",
        "items":          items,
        "count":          len(items),
        "platform":       "youtube",
    }


def _piped_search_youtube(query: str) -> Optional[str]:
    """
    Search YouTube via Piped. Returns a full YouTube watch URL for the
    first result, or None if nothing is found.
    Used as a fallback when youtubesearchpython fails.
    """
    try:
        encoded = urllib.parse.quote(query, safe="")
        data = _piped_request(f"/search?q={encoded}&filter=videos")
        items = data.get("items") or data.get("relatedStreams") or []
        if items:
            rel_url = items[0].get("url", "")
            if rel_url.startswith("/watch"):
                return f"https://www.youtube.com{rel_url}"
    except Exception:
        pass
    return None


def _search_youtube(query: str) -> str:
    """
    Search YouTube for a query string. Returns a full watch URL.
    Tries youtubesearchpython first (faster), then Piped search as fallback.
    Raises Exception if both fail.
    """
    # --- Method 1: youtubesearchpython (requires httpx==0.27.0) ---
    try:
        from youtubesearchpython import VideosSearch
        results = VideosSearch(query, limit=1).result()
        if results and results.get("result"):
            return results["result"][0]["link"]
    except Exception as e:
        print(f"[search] youtubesearchpython failed: {e}")

    # --- Method 2: Piped search ---
    piped_url = _piped_search_youtube(query)
    if piped_url:
        return piped_url

    raise Exception(f"Could not find a YouTube video for: {query}")


# ---------------------------------------------------------------------------
# Cobalt API — fallback for non-YouTube platforms (Instagram, TikTok, etc.)
# ---------------------------------------------------------------------------

COBALT_INSTANCES: List[str] = [
    "https://cobalt.api.timelessnesses.me/",
    "https://cob.frytki.pl/",
    "https://cobalt.canine.tools/",
    "https://dl.caas.ovh/",
    "https://cobalt.qlvl.dev/",
    "https://api.cobalt.tools/",
]

COBALT_HEADERS: Dict[str, str] = {
    "Accept":       "application/json",
    "Content-Type": "application/json",
}

_token_cache: Dict[str, Tuple[str, float]] = {}


def _fetch_session_token(instance_url: str) -> Optional[str]:
    """Try to get a JWT from POST /session (works on non-Turnstile instances)."""
    cached = _token_cache.get(instance_url)
    if cached:
        token, expires_at = cached
        if expires_at > time.time() + 60:
            return token

    session_url = instance_url.rstrip("/") + "/session"
    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(session_url, headers=COBALT_HEADERS, json={})
        if resp.status_code == 200:
            data = resp.json()
            token = data.get("token")
            if token and isinstance(token, str):
                _token_cache[instance_url] = (token, time.time() + 50 * 60)
                return token
    except Exception:
        pass
    return None


def _call_cobalt(payload: dict) -> dict:
    """Try each Cobalt instance with session token negotiation."""
    last_error = "No Cobalt instances available."

    with httpx.Client(timeout=45.0) as client:
        for instance_url in COBALT_INSTANCES:
            try:
                token = _fetch_session_token(instance_url)
                headers = dict(COBALT_HEADERS)
                if token:
                    headers["Authorization"] = f"Bearer {token}"

                response = client.post(instance_url, json=payload, headers=headers)

                if response.status_code == 200:
                    data = response.json()
                    error_code = ""
                    if isinstance(data.get("error"), dict):
                        error_code = data["error"].get("code", "")
                    if "auth" in error_code:
                        last_error = f"{instance_url}: auth error ({error_code})"
                        continue
                    return data

                if response.status_code in (401, 403):
                    last_error = f"{instance_url}: HTTP {response.status_code}"
                    continue

                try:
                    err_body = response.json()
                except Exception:
                    err_body = response.text
                last_error = f"{instance_url}: HTTP {response.status_code} — {err_body}"

            except httpx.TimeoutException:
                last_error = f"{instance_url}: timed out"
            except httpx.ConnectError:
                last_error = f"{instance_url}: unreachable"
            except Exception as exc:
                last_error = f"{instance_url}: {exc}"

    raise Exception(f"All Cobalt instances failed. Last error: {last_error}")


def _build_cobalt_payload(
    target_url: str, format_type: str, quality: str, file_format: str
) -> dict:
    payload: dict = {"url": target_url}
    if format_type == "audio":
        payload["downloadMode"] = "audio"
        fmt = file_format.lower() if file_format.lower() in VALID_AUDIO_FORMATS else "mp3"
        payload["audioFormat"] = fmt
        bitrate = AUDIO_BITRATE_MAP.get(
            quality, AUDIO_BITRATE_MAP.get(quality.rstrip("k"), "128")
        )
        payload["audioBitrate"] = bitrate
    else:
        payload["downloadMode"] = "auto"
        payload["videoQuality"] = VIDEO_QUALITY_MAP.get(quality, "1080")
    return payload


def _extract_cobalt_url(data: dict) -> str:
    if "url" in data:
        return data["url"]
    if data.get("status") == "picker" and data.get("picker"):
        return data["picker"][0].get("url", "")
    raise Exception(f"Unexpected Cobalt response: {data}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def health_check():
    return {"status": "ok", "message": "Media Downloader API is running"}


@app.post("/api/formats")
async def get_formats(request: Request):
    """Static format list."""
    return {
        "status": "success",
        "video":  ["max", "1080", "720", "480"],
        "audio":  ["320k", "256k", "128k"],
    }


@app.get("/api/proxy")
async def proxy_download(
    url: str = Query(...),
    filename: str = Query(default="download"),
):
    """
    Stream a remote URL through our server with Content-Disposition: attachment.
    Makes every download same-origin so the browser saves files properly and
    shows the "allow multiple downloads" prompt for playlists.
    """
    safe_name = urllib.parse.quote(filename, safe="")

    async def _stream() -> AsyncIterator[bytes]:
        timeout = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0)
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True
        ) as client:
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
    Resolve the source URL and return a direct stream URL.
    • Spotify → search YouTube → Piped stream URL
    • YouTube → Piped stream URL
    • Other (Instagram / TikTok / etc.) → Cobalt API
    """
    try:
        target_url = request.url

        # ------------------------------------------------------------------
        # Spotify single track → find on YouTube → get Piped stream
        # ------------------------------------------------------------------
        if is_spotify_url(request.url):
            if request.is_playlist:
                raise HTTPException(
                    status_code=400,
                    detail="Spotify playlists are downloaded track-by-track.",
                )

            from spotify_api import get_spotify_content_info

            info = get_spotify_content_info(request.url)
            if info.get("type") != "single":
                raise Exception("URL does not point to a single Spotify track.")

            track = info["data"]
            search_query = (
                f"{track.get('artist', '')} {track.get('title', '')}".strip()
            )

            # Search YouTube (youtubesearchpython → Piped fallback)
            target_url = await asyncio.to_thread(_search_youtube, search_query)

        # ------------------------------------------------------------------
        # YouTube (including Spotify-resolved) → Piped stream URL
        # ------------------------------------------------------------------
        if _is_youtube_url(target_url):
            video_id = _extract_yt_video_id(target_url)
            if not video_id:
                raise Exception(
                    f"Could not extract YouTube video ID from: {target_url}"
                )

            stream_url, actual_ext = await asyncio.to_thread(
                _piped_get_stream_url,
                video_id,
                request.format_type,
                request.quality,
            )
            return {
                "task_id":       "stream",
                "url":           stream_url,
                "actual_format": actual_ext,
            }

        # ------------------------------------------------------------------
        # Other platforms → Cobalt
        # ------------------------------------------------------------------
        payload = _build_cobalt_payload(
            target_url, request.format_type, request.quality, request.file_format
        )
        data = _call_cobalt(payload)
        download_url = _extract_cobalt_url(data)
        return {
            "task_id":       "cobalt",
            "url":           download_url,
            "actual_format": request.file_format,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/playlist-info")
async def get_playlist_info(request: PlaylistRequest):
    """
    Return playlist / track metadata for YouTube or Spotify URLs.

    Design:
    ─ Spotify playlists: item.id = full Spotify track URL.
      /api/download resolves each one → YouTube → Piped at download time.
    ─ YouTube playlists: Piped /playlists/{id} with pagination.
    ─ Single YouTube videos: Piped /streams/{id} for real title + thumbnail.
    """
    try:
        url = request.url

        # ------------------------------------------------------------------
        # Spotify
        # ------------------------------------------------------------------
        if is_spotify_url(url):
            playlist_info = await get_spotify_details(url)
            content_type = playlist_info.get("type")

            if content_type == "single":
                playlist_info["platform"] = "spotify"
                return playlist_info

            # Playlist / album — convert bare track IDs → full Spotify URLs
            items = playlist_info.get("items", [])
            for item in items:
                track_id = item.get("id", "")
                if track_id and not track_id.startswith("http"):
                    item["id"] = f"https://open.spotify.com/track/{track_id}"

            playlist_info["items"] = items
            playlist_info["platform"] = "spotify"
            if "type" not in playlist_info:
                playlist_info["type"] = "playlist"
            return playlist_info

        # ------------------------------------------------------------------
        # YouTube playlist
        # ------------------------------------------------------------------
        playlist_id = _extract_yt_playlist_id(url)
        if playlist_id:
            try:
                return await asyncio.to_thread(
                    _piped_get_playlist, playlist_id
                )
            except Exception as err:
                print(f"[playlist-info] Piped playlist error: {err}")
                # Fall through to single-video path

        # ------------------------------------------------------------------
        # Single YouTube video — real title via Piped
        # ------------------------------------------------------------------
        video_id = _extract_yt_video_id(url)
        if video_id:
            try:
                info = await asyncio.to_thread(
                    _piped_get_video_info, video_id
                )
                return {
                    "type":     "single",
                    "data":     {
                        "title":     info["title"],
                        "artist":    info["author"],
                        "thumbnail": info["thumbnail"],
                    },
                    "platform": "youtube",
                }
            except Exception as err:
                print(f"[playlist-info] Piped video info error: {err}")

        # Fallback — couldn't extract info
        return {
            "type":     "single",
            "data":     {"title": "Media", "artist": "", "thumbnail": None},
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