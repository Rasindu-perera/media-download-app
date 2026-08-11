"""
Media Downloader API — FastAPI backend
Uses Cobalt API for downloads, youtubesearchpython for metadata.
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Tuple
import httpx
import time
import os

from spotify_handler import is_spotify_url, get_spotify_details

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
    format_type: str          # 'video' or 'audio'
    quality: str              # e.g. "720p", "1080p", "128k", "320k"
    file_format: str          # mp4/webm for video; mp3/opus/m4a for audio
    is_playlist: bool = False
    selected_indices: Optional[List[int]] = None

class PlaylistRequest(BaseModel):
    url: str

# ---------------------------------------------------------------------------
# Cobalt API — multi-instance with automatic session token negotiation
#
# How auth works in Cobalt v10:
#   1. Call POST /session on the instance.
#   2. If the instance has NO Turnstile configured → gets back {"token":"<JWT>"}
#      immediately. Use it as  Authorization: Bearer <JWT>.
#   3. If Turnstile is required → challenge response returned → skip instance.
#   4. If no auth is configured at all → POST / works without any token.
#
# We try every instance in COBALT_INSTANCES, getting a session token first,
# then falling back to no-token if the instance doesn't need one.
# ---------------------------------------------------------------------------

COBALT_INSTANCES: List[str] = [
    "https://cobalt.api.timelessnesses.me/",
    "https://cob.frytki.pl/",
    "https://cobalt.canine.tools/",
    "https://dl.caas.ovh/",
    "https://cobalt.qlvl.dev/",
    "https://api.cobalt.tools/",   # last resort — may require Turnstile
]

COBALT_HEADERS: Dict[str, str] = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}

# In-memory session-token cache  {instance_url: (token, expires_at_unix)}
_token_cache: Dict[str, Tuple[str, float]] = {}

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


def _fetch_session_token(instance_url: str) -> Optional[str]:
    """
    Call POST /session on a Cobalt instance.
    Returns a JWT string if the instance issues one without Turnstile,
    or None if Turnstile is required / the request fails.
    Tokens are cached per-instance for 50 minutes.
    """
    # Check cache first (keep 60-second buffer before true expiry)
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
                # Cache for 50 minutes (Cobalt JWTs are typically valid ~60 min)
                _token_cache[instance_url] = (token, time.time() + 50 * 60)
                return token
    except Exception:
        pass
    return None


def _call_cobalt(payload: dict) -> dict:
    """
    Try every instance in COBALT_INSTANCES until one succeeds:
      1. Attempt POST /session to get a JWT (skips Turnstile-locked instances).
      2. POST / with Bearer token (or without if no auth needed).
      3. On any auth error → try next instance.
    Returns the parsed JSON response on success. Raises Exception if all fail.
    No 'proxies' argument is used anywhere.
    """
    last_error = "No Cobalt instances configured."

    with httpx.Client(timeout=45.0) as client:
        for instance_url in COBALT_INSTANCES:
            try:
                # --- Step 1: try to get a session token ---
                token = _fetch_session_token(instance_url)

                headers = dict(COBALT_HEADERS)
                if token:
                    headers["Authorization"] = f"Bearer {token}"

                # --- Step 2: call the processing endpoint ---
                response = client.post(instance_url, json=payload, headers=headers)

                if response.status_code == 200:
                    data = response.json()
                    # If the instance STILL complains about auth, skip it
                    error_code = ""
                    if isinstance(data.get("error"), dict):
                        error_code = data["error"].get("code", "")
                    if "auth" in error_code:
                        last_error = f"{instance_url}: auth error ({error_code})"
                        continue
                    return data

                # 401/403 — auth failed, try next
                if response.status_code in (401, 403):
                    last_error = f"{instance_url}: HTTP {response.status_code} (auth required)"
                    continue

                # Other non-200 → record and continue
                try:
                    err_body = response.json()
                except Exception:
                    err_body = response.text
                last_error = f"{instance_url}: HTTP {response.status_code} — {err_body}"

            except httpx.TimeoutException:
                last_error = f"{instance_url}: timed out"
            except httpx.ConnectError:
                last_error = f"{instance_url}: connection refused / DNS failed"
            except Exception as exc:
                last_error = f"{instance_url}: {exc}"

    raise Exception(f"All Cobalt instances failed. Last error: {last_error}")


def _build_cobalt_payload(
    target_url: str, format_type: str, quality: str, file_format: str
) -> dict:
    """Build a Cobalt v10-compatible request payload."""
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
    """
    Extract the download URL from any Cobalt response shape:
      - {"url": "..."}                       → tunnel / redirect
      - {"status": "picker", "picker": [...]} → multi-stream (e.g. TikTok)
    """
    if "url" in data:
        return data["url"]
    if data.get("status") == "picker" and data.get("picker"):
        return data["picker"][0].get("url", "")
    raise Exception(f"Unexpected Cobalt response: {data}")


# ---------------------------------------------------------------------------
# YouTube helpers (youtubesearchpython — httpx must be pinned to 0.27.0)
# ---------------------------------------------------------------------------

def _get_yt_playlist_info(url: str) -> dict:
    """
    Fetch YouTube playlist metadata using youtubesearchpython.Playlist.
    Requires httpx==0.27.0 (pinned in requirements.txt) to avoid the
    'proxies' argument error in the unmaintained library.
    """
    from youtubesearchpython import Playlist

    playlist = Playlist(url)

    # Paginate — cap at 500 items to prevent infinite loops on huge playlists
    page_count = 0
    while playlist.hasMoreVideos and page_count < 10:
        playlist.getNextVideos()
        page_count += 1

    items = []
    for video in playlist.videos:
        thumbnails = video.get("thumbnails") or []
        thumbnail_url = thumbnails[0]["url"] if thumbnails else None
        vid_id = video.get("id", "")
        items.append({
            "id": f"https://www.youtube.com/watch?v={vid_id}" if vid_id else video.get("link", ""),
            "title": video.get("title", "Unknown"),
            "thumbnail": thumbnail_url,
            "duration": None,
        })

    playlist_title = "YouTube Playlist"
    try:
        playlist_title = playlist.info["info"]["title"]
    except (KeyError, TypeError, AttributeError):
        pass

    return {
        "type": "playlist",
        "playlist_title": playlist_title,
        "items": items,
        "count": len(items),
        "platform": "youtube",
    }


def _get_yt_video_title(url: str) -> str:
    """
    Get the title of a single YouTube video using youtubesearchpython.
    Falls back to 'YouTube Video' if anything goes wrong.
    """
    try:
        from youtubesearchpython import Video
        info = Video.getInfo(url)
        if info and isinstance(info, dict):
            return info.get("title") or "YouTube Video"
    except Exception:
        pass
    return "YouTube Video"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def health_check():
    return {"status": "ok", "message": "Media Downloader API is running"}


@app.post("/api/formats")
async def get_formats(request: Request):
    """
    Static format list. Cobalt does not require pre-fetching formats.
    """
    return {
        "status": "success",
        "video": ["max", "1080", "720", "480"],
        "audio": ["320k", "256k", "128k"],
    }


@app.post("/api/download")
async def start_download(request: DownloadRequest):
    """
    Resolve the URL (Spotify → YouTube if needed) then proxy to Cobalt.
    Spotify playlist items send their individual Spotify track URL here,
    which is resolved to YouTube one-by-one at download time.
    """
    try:
        target_url = request.url

        # ------------------------------------------------------------------
        # Spotify: resolve track URL → YouTube search → YouTube URL
        # ------------------------------------------------------------------
        if is_spotify_url(request.url):
            if request.is_playlist:
                raise HTTPException(
                    status_code=400,
                    detail="Spotify playlists must be downloaded track-by-track by the frontend.",
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
                raise Exception(
                    f"Could not find a YouTube video for Spotify track: {search_query}"
                )

        # ------------------------------------------------------------------
        # Build Cobalt payload and call (v10 schema, no proxies)
        # ------------------------------------------------------------------
        payload = _build_cobalt_payload(
            target_url=target_url,
            format_type=request.format_type,
            quality=request.quality,
            file_format=request.file_format,
        )
        data = _call_cobalt(payload)
        download_url = _extract_cobalt_url(data)
        return {"task_id": "cobalt", "url": download_url}

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/playlist-info")
async def get_playlist_info(request: PlaylistRequest):
    """
    Return playlist / track metadata for YouTube or Spotify URLs.

    Design decisions:
    ─ Spotify playlists: item.id is set to the full Spotify track URL
      (https://open.spotify.com/track/<id>). The frontend's download loop
      sends each URL to /api/download which resolves them one-by-one.
      This avoids the previous N×YouTube-search timeout at info-fetch time.

    ─ YouTube playlists: uses youtubesearchpython.Playlist (requires
      httpx==0.27.0 to avoid the 'proxies' argument error).

    ─ Single YouTube videos: uses youtubesearchpython.Video to get the
      real title instead of the placeholder "YouTube Video".
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

            # Playlist / album:
            # Convert bare Spotify track IDs → full Spotify track URLs so that
            # /api/download can detect and resolve them without re-scraping.
            items = playlist_info.get("items", [])
            for item in items:
                track_id = item.get("id", "")
                if track_id and not track_id.startswith("http"):
                    item["id"] = f"https://open.spotify.com/track/{track_id}"

            playlist_info["items"] = items
            playlist_info["platform"] = "spotify"
            # Ensure the response has the 'type' field the frontend expects
            if "type" not in playlist_info:
                playlist_info["type"] = "playlist"
            return playlist_info

        # ------------------------------------------------------------------
        # YouTube
        # ------------------------------------------------------------------
        is_yt_playlist = "list=" in url and (
            "youtube.com/playlist" in url
            or "youtube.com/watch" in url
            or "youtu.be" in url
        )

        if is_yt_playlist:
            try:
                return _get_yt_playlist_info(url)
            except Exception as err:
                # Playlist fetch failed — fall through to single-video path
                print(f"[playlist-info] Playlist extraction error: {err}")

        # Single YouTube video
        title = _get_yt_video_title(url)
        return {
            "type": "single",
            "data": {
                "title": title,
                "artist": "",
                "thumbnail": None,
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