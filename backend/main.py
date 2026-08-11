from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import httpx
import os

from spotify_handler import is_spotify_url, get_spotify_details

app = FastAPI(title="Media Downloader API (Cobalt)")

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
    file_format: str          # mp4/webm for video; mp3/m4a/opus for audio
    is_playlist: bool = False
    selected_indices: Optional[List[int]] = None

class PlaylistRequest(BaseModel):
    url: str

# ---------------------------------------------------------------------------
# Cobalt API — multi-instance fallback chain
# api.cobalt.tools now requires JWT auth; we try public community instances
# in order and use the first one that succeeds.
# ---------------------------------------------------------------------------

# List of public Cobalt instances to try in order.
# Add/remove instances here if they go offline or require auth.
COBALT_INSTANCES = [
    "https://cobalt.api.timelessnesses.me/",
    "https://cob.frytki.pl/",
    "https://cobalt.canine.tools/",
    "https://dl.caas.ovh/",
    "https://api.cobalt.tools/",          # kept as last resort (may need auth)
]

COBALT_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}

# Map the quality strings the frontend sends → Cobalt videoQuality values
VIDEO_QUALITY_MAP = {
    "max":   "max",
    "4320":  "4320", "4320p": "4320",
    "2160":  "2160", "2160p": "2160", "4k": "2160", "4K": "2160",
    "1440":  "1440", "1440p": "1440",
    "1080":  "1080", "1080p": "1080",
    "720":   "720",  "720p":  "720",
    "480":   "480",  "480p":  "480",
    "360":   "360",  "360p":  "360",
    "240":   "240",  "240p":  "240",
    "144":   "144",  "144p":  "144",
}

# Map quality strings for audio → Cobalt audioBitrate (kbps number as string)
AUDIO_BITRATE_MAP = {
    "320k": "320", "320": "320",
    "256k": "256", "256": "256",
    "128k": "128", "128": "128",
    "96k":  "96",  "96":  "96",
    "64k":  "64",  "64":  "64",
}

VALID_AUDIO_FORMATS = {"mp3", "ogg", "wav", "opus", "best"}


def _call_cobalt(payload: dict) -> dict:
    """
    Try each Cobalt instance in COBALT_INSTANCES until one succeeds.
    Skips instances that return auth errors (jwt.missing / api.auth.*).
    Returns the full JSON response dict on success, raises Exception if all fail.
    No 'proxies' argument is used anywhere — direct httpx calls only.
    """
    last_error = "No Cobalt instances available."

    with httpx.Client(timeout=45.0) as client:
        for instance_url in COBALT_INSTANCES:
            try:
                response = client.post(instance_url, json=payload, headers=COBALT_HEADERS)

                if response.status_code == 200:
                    data = response.json()
                    # Skip instances that return an auth error
                    error_code = (data.get("error") or {}).get("code", "")
                    if "auth" in error_code:
                        last_error = f"{instance_url} requires auth: {error_code}"
                        continue
                    return data

                # 401/403 → auth required → try next instance
                if response.status_code in (401, 403):
                    last_error = f"{instance_url} returned HTTP {response.status_code} (auth required)"
                    continue

                # Any other non-200 status → record and try next
                try:
                    err_body = response.json()
                except Exception:
                    err_body = response.text
                last_error = f"{instance_url} returned HTTP {response.status_code}: {err_body}"

            except httpx.TimeoutException:
                last_error = f"{instance_url} timed out"
            except httpx.ConnectError:
                last_error = f"{instance_url} connection refused"
            except Exception as exc:
                last_error = f"{instance_url} error: {exc}"

    raise Exception(f"All Cobalt instances failed. Last error: {last_error}")


def _build_cobalt_payload(target_url: str, format_type: str, quality: str, file_format: str) -> dict:
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
        vq = VIDEO_QUALITY_MAP.get(quality, "1080")
        payload["videoQuality"] = vq

    return payload


def _extract_url_from_cobalt_response(data: dict) -> str:
    """
    Extract the final download URL from any Cobalt response shape:
      - { url: "..." }                        → tunnel/redirect
      - { status: "picker", picker: [...] }   → multi-stream (e.g. TikTok)
    """
    if "url" in data:
        return data["url"]

    if data.get("status") == "picker" and data.get("picker"):
        first = data["picker"][0]
        return first.get("url", "")

    raise Exception(f"Cobalt returned an unexpected response shape: {data}")


# ---------------------------------------------------------------------------
# YouTube helpers (use yt-dlp for metadata — NO download, NO IP ban risk)
# ---------------------------------------------------------------------------

def _get_youtube_playlist_info(url: str) -> dict:
    """
    Use yt-dlp with extract_flat=True to fetch YouTube playlist metadata
    without downloading anything. This replaces the broken youtubesearchpython
    Playlist class.
    """
    import yt_dlp

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,   # Only fetch playlist structure, not per-video metadata
        "skip_download": True,
        "ignoreerrors": True,
    }

    # Use cookies if available
    cookies_path = os.path.join(os.path.dirname(__file__), "cookies.txt")
    if os.path.exists(cookies_path):
        ydl_opts["cookiefile"] = cookies_path

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise Exception("yt-dlp returned no info for the URL.")

    entries = info.get("entries") or []
    items = []
    for entry in entries:
        if not entry:
            continue
        video_id = entry.get("id", "")
        video_url = entry.get("url") or (f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
        thumbnails = entry.get("thumbnails") or []
        thumbnail_url = thumbnails[-1]["url"] if thumbnails else (
            f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else None
        )
        items.append({
            "id": video_url,
            "title": entry.get("title") or entry.get("webpage_url_basename") or "Unknown",
            "thumbnail": thumbnail_url,
            "duration": entry.get("duration"),
        })

    return {
        "type": "playlist",
        "playlist_title": info.get("title") or "YouTube Playlist",
        "items": items,
        "count": len(items),
        "platform": "youtube",
    }


def _get_youtube_video_title(url: str) -> str:
    """
    Use yt-dlp to get just the title of a single YouTube video (no download).
    """
    import yt_dlp

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": True,
    }
    cookies_path = os.path.join(os.path.dirname(__file__), "cookies.txt")
    if os.path.exists(cookies_path):
        ydl_opts["cookiefile"] = cookies_path

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return info.get("title") or "YouTube Video"
    except Exception:
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
    Spotify playlist items send individual Spotify track URLs here, which
    are resolved to YouTube one-by-one at download time.
    """
    try:
        target_url = request.url

        # ------------------------------------------------------------------
        # Spotify track (single OR playlist item) → resolve to YouTube first
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
            search_query = f"{track.get('artist', '')} {track.get('title', '')}".strip()
            videos_search = VideosSearch(search_query, limit=1)
            results = videos_search.result()

            if results and results.get("result"):
                target_url = results["result"][0]["link"]
            else:
                raise Exception(
                    f"Could not find a YouTube video matching Spotify track: {search_query}"
                )

        # ------------------------------------------------------------------
        # Build payload and call Cobalt (v10 schema, no proxies, multi-instance)
        # ------------------------------------------------------------------
        payload = _build_cobalt_payload(
            target_url=target_url,
            format_type=request.format_type,
            quality=request.quality,
            file_format=request.file_format,
        )

        data = _call_cobalt(payload)
        download_url = _extract_url_from_cobalt_response(data)
        return {"task_id": "cobalt", "url": download_url}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/playlist-info")
async def get_playlist_info(request: PlaylistRequest):
    """
    Return playlist / track info for YouTube or Spotify URLs.

    Key design decisions:
    - Spotify playlists: returns Spotify track URLs as item.id (NOT YouTube URLs).
      The frontend sends each item.id to /api/download which resolves them one-by-one.
      This avoids the previous timeout caused by doing N YouTube searches upfront.
    - YouTube playlists: uses yt-dlp (extract_flat) for reliable, fast metadata.
    - Single YouTube videos: returns 'single' type with the actual video title.
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
                # Single Spotify track — return directly with platform tag
                playlist_info["platform"] = "spotify"
                return playlist_info

            # Playlist / album — return Spotify track URLs directly as item.id.
            # /api/download will resolve each Spotify URL → YouTube at download time.
            items = playlist_info.get("items", [])
            for item in items:
                spotify_track_id = item.get("id", "")
                # Replace bare track ID with a full Spotify URL so /api/download
                # can detect it as a Spotify URL and resolve it properly.
                if spotify_track_id and not spotify_track_id.startswith("http"):
                    item["id"] = f"https://open.spotify.com/track/{spotify_track_id}"

            playlist_info["items"] = items
            playlist_info["platform"] = "spotify"
            return playlist_info

        # ------------------------------------------------------------------
        # YouTube — detect playlist vs single video
        # ------------------------------------------------------------------
        is_yt_playlist = (
            "list=" in url and
            ("youtube.com/playlist" in url or "youtube.com/watch" in url or "youtu.be" in url)
        )

        if is_yt_playlist:
            try:
                return _get_youtube_playlist_info(url)
            except Exception as playlist_err:
                # If yt-dlp fails, fall through to single-video response
                print(f"Playlist extraction failed: {playlist_err}")

        # Single YouTube video — get actual title with yt-dlp
        title = _get_youtube_video_title(url)
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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)