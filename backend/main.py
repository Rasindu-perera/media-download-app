from config import GLOBAL_TMP_DIR
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os
import sys
import uuid
from typing import Optional, List, Dict
import shutil
import atexit
import time
import multiprocessing
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles

from downloader import (
    download_video, download_audio, get_available_formats, 
    DownloadProgress, get_playlist_details, download_playlist
)
from spotify_handler import (
    is_spotify_url, get_spotify_details, download_spotify_track, download_spotify_playlist
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    yield
    # Shutdown logic
    for file in os.listdir(GLOBAL_TMP_DIR):
        try:
            os.remove(os.path.join(GLOBAL_TMP_DIR, file))
        except Exception:
            pass

app = FastAPI(title="Media Downloader API", lifespan=lifespan)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create tmp directory if it doesn't exist
os.makedirs(GLOBAL_TMP_DIR, exist_ok=True)

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
        import urllib.request
        import json
        try:
            req = urllib.request.Request(f"https://www.tikwm.com/api/?url={request.url}", headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
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


def cleanup_download_file(path: str):
    """Clean up parent folder if applicable after moving."""
    try:
        if os.path.exists(path):
            os.remove(path)
            print(f"Cleaned up file: {path}")
            
        # If it was a zip file from a playlist, clean up the original playlist directory
        if path.endswith('.zip'):
            folder_path = path[:-4]
            if os.path.exists(folder_path) and os.path.isdir(folder_path):
                shutil.rmtree(folder_path)
                print(f"Cleaned up folder: {folder_path}")
    except Exception as e:
        print(f"Error during cleanup: {e}")

@app.get("/api/download/{task_id}")
async def get_download(task_id: str, background_tasks: BackgroundTasks):
    """Move the downloaded file to Downloads and return success JSON"""
    if task_id not in download_progress:
        raise HTTPException(status_code=404, detail="Task not found")
    
    progress = download_progress[task_id]
    
    if progress.status != "completed":
        raise HTTPException(status_code=400, detail="Download not completed")
    
    if not os.path.exists(progress.file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    try:
        downloads_dir = os.path.join(os.path.expanduser('~'), 'Downloads')
        os.makedirs(downloads_dir, exist_ok=True)
        filename = os.path.basename(progress.file_path)
        destination = os.path.join(downloads_dir, filename)
        
        if os.path.exists(destination):
            try:
                os.remove(destination)
            except Exception:
                pass
                
        shutil.move(progress.file_path, destination)
        
        # Schedule cleanup of potential leftover playlist directories
        background_tasks.add_task(cleanup_download_file, progress.file_path)
        
        return {
            "status": "success", 
            "message": "Saved to Downloads", 
            "file_name": filename,
            "path": destination
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to move file to Downloads: {str(e)}")

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
            import urllib.request
            import json
            req = urllib.request.Request(f"https://www.tikwm.com/api/?url={request.url}", headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
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
        temp_dir = GLOBAL_TMP_DIR
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

# Serve React Frontend Build
if getattr(sys, 'frozen', False):
    # If running as PyInstaller executable
    frontend_dir = os.path.join(sys._MEIPASS, 'frontend', 'build')
else:
    # If running locally
    frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend', 'build')

if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
else:
    @app.get("/")
    async def root():
        return {"message": "API is running. Frontend build not found at " + frontend_dir}

if __name__ == "__main__":
    import uvicorn
    import logging
    import threading
    import webview
    
    # Set up logging to see detailed output
    logging.basicConfig(level=logging.DEBUG)
    multiprocessing.freeze_support()
    
    def run_api():
        # Setting reload=False is crucial inside a thread and when frozen by PyInstaller
        uvicorn.run(app, host="127.0.0.1", port=8000, reload=False, log_level="debug")

    # Run Uvicorn in a background daemon thread so it dies when the main window exits
    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()
    
    # Create the Desktop window pointing to our local server
    webview.create_window('Media Downloader', 'http://127.0.0.1:8000', width=1024, height=768)
    
    # Start the webview event loop (this blocks the main thread until the window is closed)
    webview.start()