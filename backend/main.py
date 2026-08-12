from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os
import uuid
from typing import Optional, List, Dict
import shutil
import atexit
import time

from downloader import (
    download_video, download_audio, get_available_formats, 
    DownloadProgress, get_playlist_details, download_playlist
)
from spotify_handler import (
    is_spotify_url, get_spotify_details, download_spotify_track, download_spotify_playlist
)

app = FastAPI(title="Media Downloader API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create tmp directory if it doesn't exist
os.makedirs("tmp", exist_ok=True)

# Store download progress
download_progress: Dict[str, DownloadProgress] = {}

# Update the DownloadRequest model to include playlist options
class DownloadRequest(BaseModel):
    url: str
    format_type: str  # 'video' or 'audio'
    quality: str  # Resolution for video, bitrate for audio
    file_format: str  # mp4/webm for video, mp3/m4a/opus for audio
    is_playlist: bool = False
    selected_indices: Optional[List[int]] = None

class FormatRequest(BaseModel):
    url: str

class FormatResponse(BaseModel):
    video_formats: List[dict]


class PlaylistRequest(BaseModel):
    url: str

class PlaylistItem(BaseModel):
    id: str
    title: str
    duration: Optional[int] = None
    thumbnail: Optional[str] = None

class PlaylistResponse(BaseModel):
    is_playlist: bool
    playlist_title: Optional[str] = None
    items: List[PlaylistItem] = []
    count: int = 0

@app.post("/api/formats")
def get_formats(request: FormatRequest):
    """Get available formats for a given URL"""
    try:
        if "tiktok.com" in request.url or "vm.tiktok.com" in request.url:
            return {
                "video_formats": [
                    {"quality": "HD No Watermark", "format": "mp4", "note": "(Direct)"}
                ],
                "audio_formats": [
                    {"quality": "Original Audio", "format": "mp3", "note": "(Direct)"}
                ]
            }
            
        formats = get_available_formats(request.url)
        return formats
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Update the download endpoint
@app.post("/api/download")
async def start_download(request: DownloadRequest, background_tasks: BackgroundTasks):
    """Start a download task in the background"""
    task_id = str(uuid.uuid4())
    download_progress[task_id] = DownloadProgress(status="queued", progress=0)
    
    # Add debugging output
    print(f"Download request: {request}")
    print(f"Format type: {request.format_type}")
    print(f"Quality: {request.quality}")
    print(f"File format: {request.file_format}")
      # Check if it's a Spotify URL
    is_spotify = is_spotify_url(request.url)
    print(f"Is Spotify URL: {is_spotify}")

    # Handle different platforms and formats
    if is_spotify:
        if request.is_playlist:
            background_tasks.add_task(
                download_spotify_playlist,
                request.url,
                request.quality,
                request.file_format,
                task_id,
                download_progress,
                request.selected_indices
            )
        else:
            background_tasks.add_task(
                download_spotify_track,
                request.url,
                request.quality,
                request.file_format,
                task_id,
                download_progress
            )
    elif "tiktok.com" in request.url or "vm.tiktok.com" in request.url:
        import requests
        try:
            resp = requests.get(f"https://www.tikwm.com/api/?url={request.url}")
            data = resp.json()
            if request.format_type == "audio":
                media_url = data['data']['music']
            else:
                media_url = data['data']['play']
            return {"url": media_url}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
    elif request.is_playlist:
        background_tasks.add_task(
            download_playlist,
            request.url,
            request.format_type,
            request.quality,
            request.file_format,
            task_id,
            download_progress,
            request.selected_indices
        )
    elif request.format_type == "video":
        background_tasks.add_task(
            download_video,
            request.url,
            request.quality,
            request.file_format,
            task_id,
            download_progress
        )
    else:
        background_tasks.add_task(
            download_audio,
            request.url,
            request.quality,
            request.file_format,
            task_id,
            download_progress
        )
    
    return {"task_id": task_id}

@app.get("/api/progress/{task_id}")
async def get_progress(task_id: str):
    """Get the progress of a download task"""
    if task_id not in download_progress:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return download_progress[task_id]


@app.get("/api/download/{task_id}")
async def get_download(task_id: str):
    """Get the downloaded file"""
    if task_id not in download_progress:
        raise HTTPException(status_code=404, detail="Task not found")
    
    progress = download_progress[task_id]
    
    if progress.status != "completed":
        raise HTTPException(status_code=400, detail="Download not completed")
    
    # Add debugging output
    print(f"Looking for file at: {progress.file_path}")
    print(f"File exists: {os.path.exists(progress.file_path)}")
    
    if not os.path.exists(progress.file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(
        path=progress.file_path,
        filename=os.path.basename(progress.file_path),
        media_type="application/octet-stream"
    )

@app.post("/api/playlist-info")
def get_playlist_info(request: PlaylistRequest):
    """Get information about a playlist"""
    try:
        # Check if the URL is from Spotify
        is_spotify = is_spotify_url(request.url)
        
        if is_spotify:
            # Use Spotify-specific function to handle DRM limitations
            playlist_info = get_spotify_details(request.url)
            return playlist_info
        elif "tiktok.com" in request.url or "vm.tiktok.com" in request.url:
            import requests
            resp = requests.get(f"https://www.tikwm.com/api/?url={request.url}")
            data = resp.json()
            title = data['data'].get('title', 'TikTok Video')
            return {
                "title": title, 
                "type": "video", 
                "url": request.url,
                "is_playlist": False,
                "items": [{"id": "tiktok", "title": title}],
                "count": 1
            }
        else:
            # Use regular function for other platforms
            playlist_info = get_playlist_details(request.url)
            return playlist_info
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

def cleanup_temp_files():
    """Clean up temporary files when the server shuts down"""
    print("Cleaning up temporary files...")
    try:
        temp_dir = "tmp"
        if os.path.exists(temp_dir):
            # Get a list of all files and directories in the tmp directory
            for item in os.listdir(temp_dir):
                item_path = os.path.join(temp_dir, item)
                try:
                    if os.path.isdir(item_path):
                        # Remove directory and all its contents
                        shutil.rmtree(item_path)
                    else:
                        # Remove file
                        os.remove(item_path)
                except Exception as e:
                    print(f"Error removing {item_path}: {e}")
            print("Temporary files cleaned up successfully.")
        else:
            print("No temporary directory found.")
    except Exception as e:
        print(f"Error during cleanup: {e}")

# Register the cleanup function to run when the server exits
atexit.register(cleanup_temp_files)

# Add this endpoint to manually trigger cleanup
@app.get("/api/cleanup-temp")
async def trigger_cleanup():
    """Manually trigger temporary file cleanup"""
    cleanup_temp_files()
    return {"status": "success", "message": "Temporary files cleaned up"}

@app.on_event("shutdown")
async def cleanup():
    """Clean up temporary files on shutdown"""
    for file in os.listdir("tmp"):
        try:
            os.remove(os.path.join("tmp", file))
        except:
            pass

if __name__ == "__main__":
    import uvicorn
    import logging
    # Set up logging to see detailed output
    logging.basicConfig(level=logging.DEBUG)
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="debug")