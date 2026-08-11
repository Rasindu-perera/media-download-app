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
# Cobalt API (current v10 schema — endpoint is POST /)
# ---------------------------------------------------------------------------

COBALT_ENDPOINT = "https://api.cobalt.tools/"

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

# Map quality strings for audio → Cobalt audioBitrate values (kbps, no 'k')
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
    POST to Cobalt API using the current v10 schema.
    - Endpoint: POST /
    - No 'proxies' argument — direct call only.
    Returns the full JSON response dict on HTTP 200, raises Exception otherwise.
    """
    with httpx.Client(timeout=45.0) as client:
        response = client.post(COBALT_ENDPOINT, json=payload, headers=COBALT_HEADERS)

    if response.status_code == 200:
        data = response.json()
        return data

    # Surface the error body for debugging
    try:
        err_body = response.json()
    except Exception:
        err_body = response.text

    raise Exception(
        f"Cobalt returned HTTP {response.status_code}: {err_body}"
    )


def _build_cobalt_payload(target_url: str, format_type: str, quality: str, file_format: str) -> dict:
    """Build a Cobalt v10-compatible request payload."""
    payload: dict = {"url": target_url}

    if format_type == "audio":
        payload["downloadMode"] = "audio"
        # audioFormat
        fmt = file_format.lower() if file_format.lower() in VALID_AUDIO_FORMATS else "mp3"
        payload["audioFormat"] = fmt
        # audioBitrate — strip trailing 'k' if present
        bitrate = AUDIO_BITRATE_MAP.get(quality, AUDIO_BITRATE_MAP.get(quality.rstrip("k"), "128"))
        payload["audioBitrate"] = bitrate
    else:
        payload["downloadMode"] = "auto"
        vq = VIDEO_QUALITY_MAP.get(quality, "1080")
        payload["videoQuality"] = vq

    return payload


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def health_check():
    return {"status": "ok", "message": "Media Downloader API is running"}


@app.post("/api/formats")
async def get_formats(request: Request):
    """
    Static format list — Cobalt does not require pre-fetching formats.
    Also returns single-track info for Spotify URLs so the frontend can
    display the title without a separate call.
    """
    return {
        "status": "success",
        "video": ["max", "1080", "720", "480"],
        "audio": ["320k", "256k", "128k"],
    }


@app.post("/api/download")
async def start_download(request: DownloadRequest):
    """Resolve the URL (Spotify → YouTube if needed) then proxy to Cobalt."""
    try:
        target_url = request.url

        # ------------------------------------------------------------------
        # Spotify single-track → resolve to YouTube first
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
        # Build payload and call Cobalt (v10 schema, no proxies)
        # ------------------------------------------------------------------
        payload = _build_cobalt_payload(
            target_url=target_url,
            format_type=request.format_type,
            quality=request.quality,
            file_format=request.file_format,
        )

        data = _call_cobalt(payload)

        # Cobalt v10 success: status == "tunnel" or "redirect", url key present
        if "url" in data:
            return {"task_id": "cobalt", "url": data["url"]}

        # Cobalt sometimes returns {status: "picker", picker: [...]} for multi-stream
        if data.get("status") == "picker" and data.get("picker"):
            # Return the first item's URL
            first = data["picker"][0]
            return {"task_id": "cobalt", "url": first.get("url", "")}

        raise Exception(f"Cobalt returned an unexpected response: {data}")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/playlist-info")
async def get_playlist_info(request: PlaylistRequest):
    """
    Return playlist/track info for YouTube or Spotify URLs.
    For single YouTube videos, returns a 'single' type response instead of crashing.
    """
    try:
        from youtubesearchpython import Playlist, VideosSearch

        # ------------------------------------------------------------------
        # Spotify
        # ------------------------------------------------------------------
        if is_spotify_url(request.url):
            playlist_info = await get_spotify_details(request.url)

            # For single Spotify tracks, return as-is (frontend handles 'single' type)
            if playlist_info.get("type") == "single":
                playlist_info["platform"] = "spotify"
                return playlist_info

            # Playlist/album: resolve each track to a YouTube URL
            direct_urls = []
            for item in playlist_info.get("items", []):
                artist = item.get("artist", "")
                title = item.get("title", "")
                search_query = f"{artist} {title}".strip()
                if not search_query:
                    continue

                videos_search = VideosSearch(search_query, limit=1)
                results = videos_search.result()
                if results and results.get("result"):
                    direct_urls.append({
                        "id": results["result"][0]["link"],
                        "title": title,
                        "artist": artist,
                        "thumbnail": item.get("thumbnail"),
                        "duration": item.get("duration"),
                    })

            playlist_info["items"] = direct_urls
            playlist_info["platform"] = "spotify"
            return playlist_info

        # ------------------------------------------------------------------
        # YouTube
        # ------------------------------------------------------------------
        url = request.url

        # Detect if this is a playlist URL
        is_playlist_url = (
            "list=" in url and
            ("youtube.com/playlist" in url or "youtube.com/watch" in url)
        )

        if is_playlist_url:
            try:
                playlist = Playlist(url)
                while playlist.hasMoreVideos:
                    playlist.getNextVideos()

                items = []
                for video in playlist.videos:
                    thumbnails = video.get("thumbnails") or []
                    thumbnail_url = thumbnails[0]["url"] if thumbnails else None
                    items.append({
                        "id": video.get("link", ""),
                        "title": video.get("title", "Unknown"),
                        "thumbnail": thumbnail_url,
                        "duration": None,
                    })

                playlist_title = "Unknown Playlist"
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
            except Exception as playlist_err:
                # Fall through to single-video response
                pass

        # Single YouTube video — return 'single' type so frontend can show title
        return {
            "type": "single",
            "data": {
                "title": "YouTube Video",
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