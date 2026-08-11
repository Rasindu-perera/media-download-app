from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict
import requests
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

class DownloadRequest(BaseModel):
    url: str
    format_type: str  # 'video' or 'audio'
    quality: str  # Resolution for video, bitrate for audio
    file_format: str  # mp4/webm for video, mp3/m4a/opus for audio
    is_playlist: bool = False
    selected_indices: Optional[List[int]] = None

class FormatRequest(BaseModel):
    url: str

class PlaylistRequest(BaseModel):
    url: str

@app.post("/api/formats")
async def get_formats(request: Request):
    """Get available formats for a given URL (Dummy endpoint for Cobalt)"""
    return {
        "status": "success",
        "video": ["max", "1080", "720", "480"],
        "audio": ["320k", "256k", "128k"]
    }

@app.post("/api/download")
async def start_download(request: DownloadRequest):
    """Start a download task by proxying to Cobalt API"""
    try:
        target_url = request.url
        
        # Check if the URL is a Spotify URL
        is_spotify = is_spotify_url(request.url)

        if is_spotify:
            if request.is_playlist:
                raise HTTPException(status_code=400, detail="Playlists must be downloaded sequentially by the frontend.")
            else:
                # For Spotify single tracks, resolve to YouTube using youtube-search-python
                from spotify_api import get_spotify_content_info
                from youtubesearchpython import VideosSearch
                info = get_spotify_content_info(request.url)
                if info["type"] == "single":
                    track = info["data"]
                    search_query = f"{track['artist']} {track['title']}"
                    videos_search = VideosSearch(search_query, limit=1)
                    results = videos_search.result()
                    if results and 'result' in results and len(results['result']) > 0:
                        target_url = results['result'][0]['link']
                    else:
                        raise Exception("Could not find matching YouTube video for Spotify track")
                else:
                    raise Exception("Not a single track")

        # Construct Cobalt JSON payload
        cobalt_payload = {
            "url": target_url
        }
        
        if request.format_type == "audio":
            cobalt_payload["isAudioOnly"] = True
            cobalt_payload["aFormat"] = request.file_format if request.file_format in ["mp3", "ogg", "wav", "opus"] else "mp3"
        else:
            cobalt_payload["isAudioOnly"] = False
            quality_map = {"1080p": "1080", "720p": "720", "480p": "480", "4K": "2160"}
            cobalt_payload["vQuality"] = quality_map.get(request.quality, "1080")
            
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        
        # Use Cobalt API (Proxy as requested)
        response = requests.post("https://api.cobalt.tools/api/json", json=cobalt_payload, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            if "url" in data:
                return {"task_id": "cobalt", "url": data["url"]}
            else:
                raise Exception(f"Cobalt API Error: {data}")
        else:
            # Fallback to base url if v7 path fails
            response_v10 = requests.post("https://api.cobalt.tools/", json=cobalt_payload, headers=headers)
            if response_v10.status_code == 200:
                data = response_v10.json()
                if "url" in data:
                    return {"task_id": "cobalt", "url": data["url"]}
            raise Exception(f"Cobalt HTTP Error {response.status_code}: {response.text}")
            
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/playlist-info")
async def get_playlist_info(request: PlaylistRequest):
    """Get information about a playlist using youtube-search-python"""
    try:
        from youtubesearchpython import Playlist, VideosSearch
        is_spotify = is_spotify_url(request.url)
        
        if is_spotify:
            playlist_info = await get_spotify_details(request.url)
            # Fetch direct URLs for each item
            direct_urls = []
            for item in playlist_info.get("items", []):
                search_query = f"{item['artist']} {item['title']}"
                videos_search = VideosSearch(search_query, limit=1)
                results = videos_search.result()
                if results and 'result' in results and len(results['result']) > 0:
                    direct_urls.append({
                        "id": results['result'][0]['link'], # Pass URL instead of ID
                        "title": item['title'],
                        "artist": item['artist'],
                        "thumbnail": item['thumbnail']
                    })
            playlist_info["items"] = direct_urls
            playlist_info["platform"] = "spotify"
            return playlist_info
        else:
            # Use youtube-search-python for YouTube Playlist
            playlist = Playlist(request.url)
            while playlist.hasMoreVideos:
                playlist.getNextVideos()
            
            items = []
            for video in playlist.videos:
                items.append({
                    "id": video['link'], # Direct URL
                    "title": video['title'],
                    "thumbnail": video['thumbnails'][0]['url'] if video['thumbnails'] else None
                })
                
            return {
                "type": "playlist",
                "playlist_title": playlist.info['info']['title'],
                "items": items,
                "count": len(items),
                "platform": "youtube"
            }
            
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)