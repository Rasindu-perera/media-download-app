from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import httpx
import os

from spotify_handler import (
    is_spotify_url, get_spotify_details
)

app = FastAPI(title="Media Downloader API (Cobalt)")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins (Vercel, Render, local)
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class DownloadRequest(BaseModel):
    url: str
    format_type: str          # 'video' or 'audio'
    quality: str              # Resolution for video (e.g. "1080"), bitrate for audio (e.g. "320k")
    file_format: str          # mp4/webm for video, mp3/m4a/opus for audio
    is_playlist: bool = False
    selected_indices: Optional[List[int]] = None

class FormatRequest(BaseModel):
    url: str

class PlaylistRequest(BaseModel):
    url: str

# ---------------------------------------------------------------------------
# Cobalt API helpers
# ---------------------------------------------------------------------------

COBALT_API_URL = "https://api.cobalt.tools/api/json"
COBALT_API_URL_FALLBACK = "https://api.cobalt.tools/"

COBALT_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}

def _call_cobalt(payload: dict) -> dict:
    """
    Forward a request to the Cobalt API.
    No proxies argument is used — Cobalt is called directly.
    Returns the parsed JSON response dict on success, raises Exception on failure.
    """
    with httpx.Client(timeout=30.0) as client:
        response = client.post(COBALT_API_URL, json=payload, headers=COBALT_HEADERS)

        if response.status_code == 200:
            data = response.json()
            if "url" in data:
                return data
            raise Exception(f"Cobalt API returned unexpected body: {data}")

        # Try the base URL as a fallback (Cobalt v10+)
        fallback = client.post(COBALT_API_URL_FALLBACK, json=payload, headers=COBALT_HEADERS)
        if fallback.status_code == 200:
            data = fallback.json()
            if "url" in data:
                return data
            raise Exception(f"Cobalt fallback API returned unexpected body: {data}")

        raise Exception(
            f"Cobalt HTTP Error {response.status_code}: {response.text}"
        )

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def health_check():
    """Health check / root endpoint."""
    return {"status": "ok", "message": "Media Downloader API is running"}


@app.post("/api/formats")
async def get_formats(request: Request):
    """
    Return available download formats.
    Cobalt does not require pre-fetching formats, so this is a static response.
    """
    return {
        "status": "success",
        "video": ["max", "1080", "720", "480"],
        "audio": ["320k", "256k", "128k"]
    }


@app.post("/api/download")
async def start_download(request: DownloadRequest):
    """Start a download task by proxying to Cobalt API."""
    try:
        target_url = request.url

        # ------------------------------------------------------------------
        # Spotify: resolve the track to a YouTube URL first
        # ------------------------------------------------------------------
        if is_spotify_url(request.url):
            if request.is_playlist:
                raise HTTPException(
                    status_code=400,
                    detail="Playlists must be downloaded sequentially by the frontend."
                )

            from spotify_api import get_spotify_content_info
            from youtubesearchpython import VideosSearch

            info = get_spotify_content_info(request.url)
            if info.get("type") != "single":
                raise Exception("URL does not point to a single Spotify track.")

            track = info["data"]
            search_query = f"{track['artist']} {track['title']}"
            videos_search = VideosSearch(search_query, limit=1)
            results = videos_search.result()

            if results and results.get("result"):
                target_url = results["result"][0]["link"]
            else:
                raise Exception(
                    "Could not find a matching YouTube video for the Spotify track."
                )

        # ------------------------------------------------------------------
        # Build Cobalt payload
        # ------------------------------------------------------------------
        cobalt_payload: dict = {"url": target_url}

        if request.format_type == "audio":
            cobalt_payload["isAudioOnly"] = True
            audio_format = request.file_format if request.file_format in ["mp3", "ogg", "wav", "opus"] else "mp3"
            cobalt_payload["aFormat"] = audio_format
        else:
            cobalt_payload["isAudioOnly"] = False
            quality_map = {
                "max": "max",
                "2160": "2160", "4k": "2160", "4K": "2160",
                "1080": "1080", "1080p": "1080",
                "720": "720",  "720p": "720",
                "480": "480",  "480p": "480",
                "360": "360",  "360p": "360",
            }
            cobalt_payload["vQuality"] = quality_map.get(request.quality, "1080")

        # ------------------------------------------------------------------
        # Forward to Cobalt — NO proxies argument
        # ------------------------------------------------------------------
        data = _call_cobalt(cobalt_payload)
        return {"task_id": "cobalt", "url": data["url"]}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/playlist-info")
async def get_playlist_info(request: PlaylistRequest):
    """Get information about a playlist (YouTube or Spotify)."""
    try:
        from youtubesearchpython import Playlist, VideosSearch

        if is_spotify_url(request.url):
            playlist_info = await get_spotify_details(request.url)

            # Resolve each Spotify track to a YouTube URL
            direct_urls = []
            for item in playlist_info.get("items", []):
                search_query = f"{item.get('artist', '')} {item.get('title', '')}".strip()
                if not search_query:
                    continue
                videos_search = VideosSearch(search_query, limit=1)
                results = videos_search.result()
                if results and results.get("result"):
                    direct_urls.append({
                        "id": results["result"][0]["link"],
                        "title": item.get("title", "Unknown"),
                        "artist": item.get("artist", "Unknown"),
                        "thumbnail": item.get("thumbnail"),
                    })

            playlist_info["items"] = direct_urls
            playlist_info["platform"] = "spotify"
            return playlist_info

        else:
            # YouTube playlist
            playlist = Playlist(request.url)
            while playlist.hasMoreVideos:
                playlist.getNextVideos()

            items = []
            for video in playlist.videos:
                thumbnails = video.get("thumbnails") or []
                thumbnail_url = thumbnails[0]["url"] if thumbnails else None
                items.append({
                    "id": video["link"],
                    "title": video["title"],
                    "thumbnail": thumbnail_url,
                })

            playlist_title = "Unknown Playlist"
            try:
                playlist_title = playlist.info["info"]["title"]
            except (KeyError, TypeError):
                pass

            return {
                "type": "playlist",
                "playlist_title": playlist_title,
                "items": items,
                "count": len(items),
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