from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel
import os
import uuid
from typing import Optional, List, Dict
import shutil
import atexit
import time
import asyncio
import glob

from downloader import (
    download_video, download_audio, get_available_formats, 
    DownloadProgress, get_playlist_details, download_playlist
)
from spotify_handler import (
    is_spotify_url, get_spotify_details, download_spotify_track, download_spotify_playlist
)

app = FastAPI(title="Media Downloader API")

# Auto-cleanup task for error logs
async def cleanup_error_logs():
    while True:
        try:
            # Delete any .txt files in tmp that are older than 10 minutes, or just blindly delete them
            for f in glob.glob("tmp/*.txt"):
                if os.path.exists(f):
                    os.remove(f)
        except Exception:
            pass
        await asyncio.sleep(600)  # Wait 10 minutes

@app.on_event("startup")
async def startup_event():
    # Removed cookie handling as per Impossible Travel fix
    asyncio.create_task(cleanup_error_logs())

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins (Vercel, Render, local)
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
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
    audio_formats: List[dict]

class PlaylistRequest(BaseModel):
    url: str

# Removed PlaylistItem and PlaylistResponse to allow dynamic returning

@app.post("/api/formats")
async def get_formats(request: FormatRequest):
    """Get available formats for a given URL"""
    try:
        if is_spotify_url(request.url):
            return {
                'video_formats': [],
                'audio_formats': [
                    {'format_id': 'bestaudio', 'quality': 320, 'ext': 'mp3', 'filesize': None, 'format_note': 'Best Audio (MP3)'},
                    {'format_id': 'bestaudio', 'quality': 256, 'ext': 'm4a', 'filesize': None, 'format_note': 'Best Audio (M4A)'}
                ]
            }
        formats = await get_available_formats(request.url)
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
      
    # Block 1080p/4K on free tier
    if request.quality in ["1080p", "1440p", "2160p"]:
        raise HTTPException(status_code=400, detail="1080p and 4K downloads are blocked on the free server tier.")
        
    # Check if the URL is a Spotify URL
    is_spotify = is_spotify_url(request.url)
    print(f"Is Spotify URL: {is_spotify}")

    # Handle different platforms and formats
    if is_spotify:
        if request.is_playlist:
            # We don't download Spotify playlists directly anymore; the frontend handles it sequentially
            raise HTTPException(status_code=400, detail="Playlists must be downloaded sequentially by the frontend.")
        else:
            # For Spotify single tracks, use ytsearch1:
            from spotify_api import get_spotify_content_info
            try:
                info = get_spotify_content_info(request.url)
                if info["type"] == "single":
                    track = info["data"]
                    search_query = f"ytsearch1:{track['artist']} {track['title']}"
                    # Use the YouTube downloader with the search query
                    background_tasks.add_task(
                        download_audio,
                        search_query,
                        request.quality,
                        request.file_format,
                        task_id,
                        download_progress
                    )
                else:
                    raise Exception("Not a single track")
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e))
    else:
        if request.is_playlist:
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
        media_type="application/octet-stream",
        background=BackgroundTask(os.remove, progress.file_path)
    )

@app.post("/api/playlist-info")
async def get_playlist_info(request: PlaylistRequest):
    """Get information about a playlist"""
    try:
        # Check if the URL is from Spotify
        is_spotify = is_spotify_url(request.url)
        
        if is_spotify:
            # Use Spotify-specific function to handle DRM limitations
            playlist_info = await get_spotify_details(request.url)
            return playlist_info
        else:
            # Use regular function for other platforms
            playlist_info = await get_playlist_details(request.url)
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

async def auto_cleanup_loop():
    """Periodically clean up files older than 10 minutes in the tmp directory"""
    while True:
        try:
            temp_dir = "tmp"
            if os.path.exists(temp_dir):
                current_time = time.time()
                for root, dirs, files in os.walk(temp_dir, topdown=False):
                    for file in files:
                        file_path = os.path.join(root, file)
                        # Check if file is older than 10 minutes (600 seconds)
                        if current_time - os.path.getctime(file_path) > 600:
                            try:
                                os.remove(file_path)
                            except:
                                pass
                    for dir in dirs:
                        dir_path = os.path.join(root, dir)
                        try:
                            # Remove directory if empty
                            if not os.listdir(dir_path):
                                os.rmdir(dir_path)
                        except:
                            pass
        except Exception as e:
            print(f"Error in auto cleanup loop: {e}")
        
        # Sleep for 5 minutes before checking again
        await asyncio.sleep(300)

@app.on_event("startup")
async def startup_event():
    """Start background tasks on startup"""
    asyncio.create_task(auto_cleanup_loop())

@app.on_event("shutdown")
async def cleanup():
    """Clean up all temporary files on shutdown"""
    cleanup_temp_files()

if __name__ == "__main__":
    import uvicorn
    import logging
    # Set up logging to see detailed output
    logging.basicConfig(level=logging.DEBUG)
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True, log_level="debug")