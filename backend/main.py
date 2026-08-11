"""
Media Downloader API — FastAPI backend

Download strategy:
  • YouTube / Spotify (resolved to YouTube): pytubefix
    → extracts signed CDN stream URLs server-side; file transfer is
      browser ↔ YouTube CDN (no Render bandwidth used).
  • Other platforms (Instagram, TikTok, etc.): Cobalt API fallback chain.

/api/proxy streams any remote URL through our server with proper
Content-Disposition headers, making downloads same-origin so the browser
shows the "allow multiple downloads" prompt and saves files to disk.
"""

import asyncio
import os
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
# URL classification
# ---------------------------------------------------------------------------

def _is_youtube_url(url: str) -> bool:
    url_l = url.lower()
    return "youtube.com" in url_l or "youtu.be" in url_l


def _normalize_yt_playlist_url(url: str) -> str:
    """
    Extract the list= parameter from any YouTube URL and return a clean
    https://www.youtube.com/playlist?list=<ID> URL.
    youtubesearchpython.Playlist requires this exact format.
    """
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    if "list" in params:
        return f"https://www.youtube.com/playlist?list={params['list'][0]}"
    return url

# ---------------------------------------------------------------------------
# pytubefix helpers — used for ALL YouTube downloads
# ---------------------------------------------------------------------------

def _pytubefix_get_info(url: str) -> dict:
    """
    Return title, author, thumbnail for a YouTube video using pytubefix.
    Falls back to placeholder values on any error.
    """
    try:
        from pytubefix import YouTube
        yt = YouTube(url)
        return {
            "title":     yt.title or "YouTube Video",
            "author":    yt.author or "",
            "thumbnail": yt.thumbnail_url,
        }
    except Exception:
        return {"title": "YouTube Video", "author": "", "thumbnail": None}


def _pytubefix_get_stream_url(
    url: str, format_type: str, quality: str, file_format: str
) -> Tuple[str, str]:
    """
    Return (stream_url, actual_file_extension) for a YouTube video.

    Audio: returns the best audio-only stream (typically m4a).
    Video: returns the best progressive (video+audio merged) mp4 stream
           at the requested quality, or the highest available progressive
           stream if the exact quality is not present.
           Note: progressive streams cap at 720p; 1080p+ requires adaptive
           streams which need server-side merging (not supported here).
    """
    from pytubefix import YouTube

    yt = YouTube(url)

    if format_type == "audio":
        stream = yt.streams.get_audio_only()
        if stream is None:
            stream = yt.streams.filter(only_audio=True).first()
        if stream is None:
            raise Exception("No audio stream available for this video.")
        ext = stream.subtype or "m4a"
        return stream.url, ext

    # Video
    quality_str = quality.replace("p", "")
    stream = yt.streams.filter(
        progressive=True, file_extension="mp4", res=f"{quality_str}p"
    ).first()

    if stream is None:
        # Best available progressive stream (max 720p on YouTube)
        stream = (
            yt.streams.filter(progressive=True, file_extension="mp4")
            .order_by("resolution")
            .last()
        )

    if stream is None:
        stream = yt.streams.filter(file_extension="mp4").first()

    if stream is None:
        raise Exception("No video stream available for this video.")

    ext = stream.subtype or "mp4"
    return stream.url, ext


def _get_yt_playlist_info(url: str) -> dict:
    """
    Fetch YouTube playlist metadata using youtubesearchpython.Playlist.
    Normalizes the URL to the required ?list=... format first, and
    safely handles the case where playlist.videos is None.
    Requires httpx==0.27.0 (pinned) to avoid the 'proxies' error.
    """
    from youtubesearchpython import Playlist

    clean_url = _normalize_yt_playlist_url(url)
    playlist = Playlist(clean_url)

    if playlist.videos is None:
        raise Exception(
            f"Could not load YouTube playlist. "
            f"Make sure the URL contains a valid playlist ID (list=...)."
        )

    # Paginate — max 10 pages (~200 videos) to avoid infinite loops
    page = 0
    while playlist.hasMoreVideos and page < 10:
        playlist.getNextVideos()
        page += 1

    items: List[dict] = []
    for video in playlist.videos or []:
        thumbnails = video.get("thumbnails") or []
        thumbnail_url = thumbnails[0]["url"] if thumbnails else None
        vid_id = video.get("id", "")
        watch_url = (
            f"https://www.youtube.com/watch?v={vid_id}"
            if vid_id
            else video.get("link", "")
        )
        items.append(
            {
                "id":        watch_url,
                "title":     video.get("title", "Unknown"),
                "thumbnail": thumbnail_url,
                "duration":  None,
            }
        )

    playlist_title = "YouTube Playlist"
    try:
        playlist_title = playlist.info["info"]["title"]
    except (KeyError, TypeError, AttributeError):
        pass

    return {
        "type":           "playlist",
        "playlist_title": playlist_title,
        "items":          items,
        "count":          len(items),
        "platform":       "youtube",
    }

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

_token_cache: Dict[str, Tuple[str, float]] = {}  # {url: (token, expires_at)}


def _fetch_session_token(instance_url: str) -> Optional[str]:
    """
    Try to obtain a JWT via POST /session (only works on instances that do NOT
    have Turnstile configured). Tokens are cached per-instance for 50 minutes.
    """
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
    """
    Try every Cobalt instance; get a session token first where possible.
    Skips instances that require auth we cannot satisfy.
    No 'proxies' argument used anywhere.
    """
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
        bitrate = AUDIO_BITRATE_MAP.get(quality, AUDIO_BITRATE_MAP.get(quality.rstrip("k"), "128"))
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
    """Static format list — Cobalt does not require pre-fetching formats."""
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
    Stream a remote URL through our server with a Content-Disposition: attachment
    header. This makes every download same-origin so that:
      1. link.download attribute works (browser saves file to disk).
      2. The browser shows the 'allow multiple files' prompt for playlists.
    Uses chunked streaming — the full file is never loaded into RAM.
    """
    safe_name = urllib.parse.quote(filename, safe="")

    async def _stream() -> AsyncIterator[bytes]:
        # Connect timeout 10s, read timeout 5 minutes (large files)
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
    Resolve the source URL and return a direct stream URL.
    • Spotify → search YouTube → pytubefix stream URL
    • YouTube → pytubefix stream URL
    • Other (Instagram / TikTok / etc.) → Cobalt API
    """
    try:
        target_url = request.url

        # ------------------------------------------------------------------
        # Spotify single track → find on YouTube
        # ------------------------------------------------------------------
        if is_spotify_url(request.url):
            if request.is_playlist:
                raise HTTPException(
                    status_code=400,
                    detail="Spotify playlists are downloaded track-by-track by the frontend.",
                )

            from spotify_api import get_spotify_content_info
            from youtubesearchpython import VideosSearch

            info = get_spotify_content_info(request.url)
            if info.get("type") != "single":
                raise Exception("URL does not point to a single Spotify track.")

            track = info["data"]
            search_query = (
                f"{track.get('artist', '')} {track.get('title', '')}".strip()
            )
            results = VideosSearch(search_query, limit=1).result()
            if results and results.get("result"):
                target_url = results["result"][0]["link"]
            else:
                raise Exception(f"Could not find a YouTube match for: {search_query}")

        # ------------------------------------------------------------------
        # YouTube (including Spotify-resolved) → pytubefix
        # ------------------------------------------------------------------
        if _is_youtube_url(target_url):
            # Run synchronous pytubefix in a thread to avoid blocking the event loop
            stream_url, actual_ext = await asyncio.to_thread(
                _pytubefix_get_stream_url,
                target_url,
                request.format_type,
                request.quality,
                request.file_format,
            )
            return {
                "task_id":      "stream",
                "url":          stream_url,
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
        return {"task_id": "cobalt", "url": download_url, "actual_format": request.file_format}

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/playlist-info")
async def get_playlist_info(request: PlaylistRequest):
    """
    Return playlist / track metadata for YouTube or Spotify URLs.

    Key design decisions:
    ─ Spotify playlists: item.id = full Spotify track URL.
      /api/download receives each one and resolves → YouTube → pytubefix.
      No YouTube searches happen at info-fetch time (avoids timeout).
    ─ YouTube playlists: youtubesearchpython.Playlist with URL normalization.
    ─ Single YouTube videos: pytubefix title (real title, not placeholder).
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
        is_yt_playlist = "list=" in url and (
            "youtube.com/playlist" in url
            or "youtube.com/watch" in url
            or "youtu.be" in url
        )

        if is_yt_playlist:
            try:
                return await asyncio.to_thread(_get_yt_playlist_info, url)
            except Exception as err:
                print(f"[playlist-info] Playlist extraction error: {err}")
                # Fall through to single-video path

        # ------------------------------------------------------------------
        # Single YouTube video — use pytubefix for real title
        # ------------------------------------------------------------------
        info = await asyncio.to_thread(_pytubefix_get_info, url)
        return {
            "type":     "single",
            "data":     {
                "title":     info["title"],
                "artist":    info["author"],
                "thumbnail": info["thumbnail"],
            },
            "platform": "youtube",
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