import shutil
from config import GLOBAL_TMP_DIR
import os
import json
import yt_dlp

import sys
def get_ffmpeg_path():
    """Get the correct path to ffmpeg.exe (frozen or local)"""
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        # User said ffmpeg.exe is in project root (one level up from backend)
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
    ffmpeg_path = os.path.join(base_path, 'ffmpeg.exe')
    
    if os.path.exists(ffmpeg_path):
        return ffmpeg_path
    return 'ffmpeg'  # Fallback to system PATH

import re
from typing import Dict, List, Optional
from dataclasses import dataclass
from utils import clean_filename, get_platform, format_spotify_filename, get_id3_metadata_dict

@dataclass
class DownloadProgress:
    status: str  # queued, downloading, completed, error
    progress: float
    file_path: Optional[str] = None
    error: Optional[str] = None
    status_text: Optional[str] = None

class ProgressHook:
    def __init__(self, task_id: str, progress_dict: Dict[str, DownloadProgress], custom_status_prefix: Optional[str] = None):
        self.task_id = task_id
        self.progress_dict = progress_dict
        self.download_count = 0
        self.custom_status_prefix = custom_status_prefix
    
    def __call__(self, d):
        if d['status'] == 'downloading':
            progress = 0
            if '_percent_str' in d:
                clean_str = d['_percent_str'].replace('%', '').strip()
                if clean_str != 'N/A' and clean_str != '':
                    try:
                        progress = float(clean_str)
                    except ValueError:
                        pass
            
            if progress == 0:
                if 'total_bytes' in d and d['total_bytes'] > 0:
                    progress = d['downloaded_bytes'] / d['total_bytes'] * 100
                elif 'total_bytes_estimate' in d and d['total_bytes_estimate'] > 0:
                    progress = d['downloaded_bytes'] / d['total_bytes_estimate'] * 100
                
            if self.custom_status_prefix:
                status_text = self.custom_status_prefix
            elif self.download_count == 0:
                status_text = "Downloading Media (Part 1)..."
            elif self.download_count == 1:
                status_text = "Downloading Audio Stream (Part 2)..."
            else:
                status_text = "Downloading additional streams..."
                
            self.progress_dict[self.task_id] = DownloadProgress(
                status="downloading",
                progress=progress,
                status_text=status_text
            )
        
        elif d['status'] == 'finished':
            self.download_count += 1
            if self.custom_status_prefix:
                status_text = f"Finalizing {self.custom_status_prefix.replace('Downloading', '').strip()}"
            elif self.download_count >= 2:
                status_text = "Merging Streams (Please wait)..."
            else:
                status_text = "Finalizing Download..."
                
            self.progress_dict[self.task_id] = DownloadProgress(
                status="processing",
                progress=100,
                status_text=status_text
            )

def get_available_formats(url: str) -> dict:
    """Get available formats for a video"""
    # Check if it's a Spotify URL - import here to avoid circular imports
    from spotify_handler import is_spotify_url
    
    # Provide default formats for Spotify
    if is_spotify_url(url):
        print("Spotify URL detected - providing audio format options")
        # Spotify supports audio formats
        return {
            'video_formats': [],
            'audio_formats': [
                {'format_id': 'mp3', 'quality': 320, 'ext': 'mp3', 'filesize': None, 'format_note': 'High quality MP3'},
                {'format_id': 'm4a', 'quality': 256, 'ext': 'm4a', 'filesize': None, 'format_note': 'M4A audio'},
                {'format_id': 'opus', 'quality': 160, 'ext': 'opus', 'filesize': None, 'format_note': 'Opus audio'}
            ]
        }
    
    # For non-Spotify URLs, proceed with normal format extraction
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Filter and categorize formats
            video_formats = []
            audio_formats = []
            
            for format in info.get('formats', []):
                # Video formats
                if format.get('vcodec') != 'none' and format.get('acodec') != 'none':
                    if format.get('height'):
                        resolution = f"{format['height']}p"
                        format_name = format.get('format_note', format.get('format_id'))
                        
                        # Only include common resolutions
                        if format['height'] in [480, 720, 1080, 2160]:
                            video_formats.append({
                                'format_id': format['format_id'],
                                'quality': resolution,
                                'ext': format.get('ext', 'mp4'),
                                'filesize': format.get('filesize'),
                                'format_note': format_name
                            })
                
                # Audio formats
                elif format.get('vcodec') == 'none' and format.get('acodec') != 'none':
                    audio_formats.append({
                        'format_id': format['format_id'],
                        'quality': format.get('abr', 0),
                        'ext': format.get('ext', 'mp3'),
                        'filesize': format.get('filesize'),
                        'format_note': format.get('format_note', '')
                    })
            
            # Deduplicate and sort formats
            video_formats = sorted(
                {f['quality']: f for f in video_formats}.values(),
                key=lambda x: int(x['quality'].replace('p', ''))
            )
            
            audio_formats = sorted(
                {f['quality']: f for f in audio_formats}.values(),
                key=lambda x: x['quality'] if x['quality'] else 0,
                reverse=True
            )
            
            return {
                'video_formats': video_formats,
                'audio_formats': audio_formats
            }
    
    except Exception as e:
        raise Exception(f"Error extracting formats: {str(e)}")

def download_video(
    url: str,
    quality: str,
    file_format: str,
    task_id: str,
    progress_dict: Dict[str, DownloadProgress]
):
    """Download a video with specified quality and format"""
    # Create tmp directory if it doesn't exist
    os.makedirs(GLOBAL_TMP_DIR, exist_ok=True)
    
    # Initialize original_height to None to prevent the UnboundLocalError
    original_height = None
    
    # Map quality string to resolution height
    quality_map = {
        "480p": 480,
        "720p": 720,
        "1080p": 1080,
        "4K": 2160
    }
    height = quality_map.get(quality, 720)
    
    # Determine platform
    is_youtube = 'youtube.com' in url or 'youtu.be' in url
    is_facebook = 'facebook.com' in url or 'fb.com' in url or 'fb.watch' in url
    is_instagram = 'instagram.com' in url
    is_tiktok = 'tiktok.com' in url
    
    # Generate unique filenames
    base_filename = f"video_{task_id}"
    temp_output = os.path.join(GLOBAL_TMP_DIR, base_filename)
    final_output = os.path.join(GLOBAL_TMP_DIR, f"{base_filename}.{file_format}")
    
    print(f"Downloading from URL: {url}")
    print(f"Platform: {'YouTube' if is_youtube else 'Facebook' if is_facebook else 'Instagram' if is_instagram else 'TikTok' if is_tiktok else 'Other'}")
    print(f"Requested quality: {quality}, target height: {height}px")
    
    try:
        # Update progress
        progress_dict[task_id] = DownloadProgress(
            status="downloading",
            progress=10,
            error="Downloading video..."
        )
        
        # Set up download options based on platform
        ydl_opts = {
            'outtmpl': temp_output,
            'progress_hooks': [ProgressHook(task_id, progress_dict)],
            'quiet': False,
            'retries': 10,
            'ffmpeg_location': get_ffmpeg_path(),
            'concurrent_fragment_downloads': 10,
            'http_chunk_size': 10485760,
            'extractor_args': {'youtube': ['player_client=android', 'player_client=web']},
            'fragment_retries': 10,
            'skip_unavailable_fragments': True,
        }
        
        # Platform-specific settings
        if is_youtube:
            # YouTube supports quality selection directly
            ydl_opts['format'] = f'bestvideo[height<={height}]+bestaudio/best[height<={height}]/best'
        elif is_facebook:
            # Facebook usually only has one format available
            ydl_opts['format'] = 'best'
        elif is_instagram:
            ydl_opts['format'] = 'best'
        elif is_tiktok:
            ydl_opts['format'] = 'best'
        else:
            # Default for other platforms
            ydl_opts['format'] = 'best'
        
        print(f"Using download options: {ydl_opts}")
        
        # Download the video
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            # Get video info for better naming
            if info and 'title' in info:
                title = info.get('title', 'video')
                safe_title = ''.join(c if c.isalnum() or c in ' -_' else '_' for c in title)
                safe_title = safe_title[:50]
                print(f"Video title: {safe_title}")
                
                # Display track information more prominently
                print("\n" + "="*50)
                print("VIDEO INFORMATION:")
                print(f"Title: {title.encode('ascii', 'ignore').decode('ascii')}")
                print(f"Target Quality: {quality}")
                print(f"Format: {file_format}")
                print(f"Output Filename: {safe_title}_{task_id}.{file_format}")
                print("="*50 + "\n")
                
                # Update final output path with title
                final_output = os.path.join(GLOBAL_TMP_DIR, f"{safe_title}_{task_id}.{file_format}")
                
            # Get downloaded file path and original height
            if '_filename' in info:
                downloaded_file = info['_filename']
                original_height = info.get('height', 0)
                print(f"Original video height: {original_height}px")
            else:
                # Search for the downloaded file
                potential_files = [os.path.join(GLOBAL_TMP_DIR, f) for f in os.listdir(GLOBAL_TMP_DIR) 
                                 if task_id in f and os.path.isfile(os.path.join(GLOBAL_TMP_DIR, f))]
                
                if potential_files:
                    downloaded_file = potential_files[0]
                    print(f"Found downloaded file: {downloaded_file}")
                else:
                    raise Exception("Could not find downloaded file")
        
        # STEP 2: Process to requested quality for non-YouTube platforms
        if not is_youtube or (original_height and original_height != height):
            progress_dict[task_id] = DownloadProgress(
                status="processing",
                progress=70,
                error=f"Processing to {quality}..."
            )
            
            import subprocess
            
            # Use FFmpeg to convert/scale the video
            ffmpeg_cmd = [
                'ffmpeg',
                '-i', downloaded_file,
                '-vf', f'scale=-1:{height}',  # Scale to requested height, maintain aspect ratio
                '-c:v', 'libx264',            # Use H.264 codec
                '-crf', '23',                 # Balance quality/size
                '-preset', 'medium',          # Balance speed/compression
                '-c:a', 'aac',                # AAC audio codec
                '-b:a', '128k',               # Audio bitrate
                '-y',                         # Overwrite output
                final_output
            ]
            
            print(f"Running FFmpeg: {' '.join(ffmpeg_cmd)}")
            
            # Run FFmpeg process
            process = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
            
            if process.returncode != 0:
                print(f"FFmpeg error: {process.stderr}")
                # Fall back to direct copy if conversion fails
                shutil.copy2(downloaded_file, final_output)
                print(f"Used direct copy instead of conversion")
        else:
            # For YouTube with correct quality, just rename if needed
            if downloaded_file != final_output:
                shutil.copy2(downloaded_file, final_output)
        
        # Verify final file exists
        if not os.path.exists(final_output):
            raise Exception("Final processed file not found")
        
        # Update progress to completed
        progress_dict[task_id] = DownloadProgress(
            status="completed",
            progress=100,
            file_path=final_output
        )
        
    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        progress_dict[task_id] = DownloadProgress(
            status="error",
            progress=0,
            error=f"Download failed: {str(e)}"
        )

def download_audio(
    url: str,
    quality: str,
    file_format: str,
    task_id: str,
    progress_dict: Dict[str, DownloadProgress]
):
    """Download audio with specified quality and format"""
    # Create tmp directory if it doesn't exist
    os.makedirs(GLOBAL_TMP_DIR, exist_ok=True)
    
    # Create a more unique filename for the output
    import uuid
    from utils import clean_filename
    
    unique_id = task_id if task_id else str(uuid.uuid4())
    base_output = os.path.join(GLOBAL_TMP_DIR, f"audio_{unique_id}")
    
    # Update progress immediately
    progress_dict[task_id] = DownloadProgress(
        status="processing",
        progress=5,
        error="Extracting track information..."
    )
    
    # Map quality string to audio bitrate
    quality_map = {
        "128k": 128,
        "256k": 256,
        "320k": 320
    }
    
    bitrate = quality_map.get(quality, 128)
    
    # Log pre-download state of tmp directory
    print(f"Files in tmp directory before download: {os.listdir(GLOBAL_TMP_DIR)}")
    
    # First extract info without downloading to get track information
    custom_filename = None
    
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'skip_download': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Display track information
            if info and 'title' in info:
                title = info.get('title', 'Unknown Title')
                artist = info.get('artist', info.get('uploader', 'Unknown Artist'))
                
                print("\n" + "="*50)
                print("TRACK INFORMATION:")
                print(f"Title: {title.encode('ascii', 'ignore').decode('ascii')}")
                print(f"Artist/Channel: {artist.encode('ascii', 'ignore').decode('ascii')}")
                print(f"Quality: {quality}")
                print(f"Format: {file_format}")
                
                # Create a clean filename
                custom_filename = clean_filename(title)
                base_output = os.path.join(GLOBAL_TMP_DIR, f"{custom_filename}_{unique_id}")
                expected_filename = f"{custom_filename}_{unique_id}.{file_format}"
                
                print(f"Output Base Filename: {base_output.encode('ascii', 'ignore').decode('ascii')}")
                print(f"Output Filename: {expected_filename.encode('ascii', 'ignore').decode('ascii')}")
                print("="*50 + "\n")
    except Exception as e:
        print(f"Warning: Could not get track information: {e}")
        # Continue with default filename
    
    # Prepare yt-dlp options with the custom filename
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': base_output,
        'progress_hooks': [ProgressHook(task_id, progress_dict)],
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': file_format,
            'preferredquality': str(bitrate),
        }],
        'quiet': False,
        'verbose': True,
        'retries': 10,
        'ffmpeg_location': get_ffmpeg_path(),
        'concurrent_fragment_downloads': 10,
        'http_chunk_size': 10485760,
        'extractor_args': {'youtube': ['player_client=android', 'player_client=web']},
        'fragment_retries': 10,
        'skip_unavailable_fragments': True,
    }
    
    try:
        # Download the audio
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print("Starting download...")
            info = ydl.extract_info(url, download=True)
            print("Download completed.")
        
        # The expected output file with extension
        expected_output = f"{base_output}.{file_format}"
        print(f"Looking for expected output file: {expected_output.encode('ascii', 'ignore').decode('ascii')}")
        
        # Verify the file exists
        if os.path.exists(expected_output):
            print(f"Found file at expected path: {expected_output.encode('ascii', 'ignore').decode('ascii')}")
        else:
            print(f"File not found at expected path. Searching in tmp directory...")
            
            # Method 1: Check for any file with the task_id in its name
            tmp_files = os.listdir(GLOBAL_TMP_DIR)
            matching_files = [f for f in tmp_files if unique_id in f and f.endswith(f".{file_format}")]
            
            if matching_files:
                expected_output = os.path.join(GLOBAL_TMP_DIR, matching_files[0])
                print(f"Found file with matching ID: {expected_output.encode('ascii', 'ignore').decode('ascii')}")
            else:
                # Method 2: Check for any file with custom filename if we have one
                if custom_filename:
                    matching_files = [f for f in tmp_files if custom_filename in f and f.endswith(f".{file_format}")]
                    if matching_files:
                        expected_output = os.path.join(GLOBAL_TMP_DIR, matching_files[0])
                        print(f"Found file with matching title: {expected_output.encode('ascii', 'ignore').decode('ascii')}")
                    else:
                        # Method 3: Check for any recently created audio file
                        audio_files = [f for f in tmp_files if f.endswith(f".{file_format}")]
                        if audio_files:
                            # Sort by creation time, newest first
                            audio_files.sort(key=lambda f: os.path.getctime(os.path.join(GLOBAL_TMP_DIR, f)), reverse=True)
                            expected_output = os.path.join(GLOBAL_TMP_DIR, audio_files[0])
                            print(f"Using most recently created audio file: {expected_output.encode('ascii', 'ignore').decode('ascii')}")
                        else:
                            raise Exception(f"Could not find any downloaded audio file with format {file_format}")
                else:
                    # Method 3: Check for any recently created audio file
                    audio_files = [f for f in tmp_files if f.endswith(f".{file_format}")]
                    if audio_files:
                        # Sort by creation time, newest first
                        audio_files.sort(key=lambda f: os.path.getctime(os.path.join(GLOBAL_TMP_DIR, f)), reverse=True)
                        expected_output = os.path.join(GLOBAL_TMP_DIR, audio_files[0])
                        print(f"Using most recently created audio file: {expected_output.encode('ascii', 'ignore').decode('ascii')}")
                    else:
                        raise Exception(f"Could not find any downloaded audio file with format {file_format}")
        
        # Update progress with file path
        progress_dict[task_id] = DownloadProgress(
            status="completed",
            progress=100,
            file_path=expected_output
        )
        
        # Display completion message with filename
        print(f"Download completed: {os.path.basename(expected_output).encode('ascii', 'ignore').decode('ascii')}")
        return expected_output
        
    except Exception as e:
        print(f"Error in download_audio: {str(e)}")
        import traceback
        traceback.print_exc()
        
        progress_dict[task_id] = DownloadProgress(
            status="error",
            progress=0,
            error=str(e)
        )
        return None

def get_playlist_details(url: str) -> dict:
    """Get details about a playlist including all video items"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': True,  # Don't extract individual videos in the playlist
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Check if it's a playlist
            if 'entries' in info:
                # It's a playlist
                playlist_items = []
                
                for entry in info['entries']:
                    # Some basic info is available directly
                    item = {
                        'id': entry.get('id', ''),
                        'title': entry.get('title', 'Unknown Title'),
                        'duration': entry.get('duration'),
                        'thumbnail': entry.get('thumbnail')
                    }
                    playlist_items.append(item)
                
                return {
                    'is_playlist': True,
                    'playlist_title': info.get('title', 'Playlist'),
                    'items': playlist_items,
                    'count': len(playlist_items)
                }
            else:
                # It's a single video
                return {
                    'is_playlist': False,
                    'items': [{
                        'id': info.get('id', ''),
                        'title': info.get('title', 'Unknown Title'),
                        'duration': info.get('duration'),
                        'thumbnail': info.get('thumbnail')
                    }],
                    'count': 1
                }
    
    except Exception as e:
        raise Exception(f"Error extracting playlist info: {str(e)}")

def download_playlist(
    url: str,
    format_type: str,
    quality: str,
    file_format: str,
    task_id: str,
    progress_dict: Dict[str, DownloadProgress],
    selected_indices: Optional[List[int]] = None
):
    """Download a playlist of videos or extract audio from them"""
    try:
        # First, get playlist info
        playlist_info = get_playlist_details(url)
        if not playlist_info['is_playlist']:
            raise Exception ("The URL does not point to a playlist")

        # Create a directory for this playlist
        playlist_title = ''.join(c if c.isalnum() or c in ' -_' else '_' for c in playlist_info['playlist_title'])
        playlist_dir = os.path.join(GLOBAL_TMP_DIR, f"playlist_{task_id}")
        os.makedirs(playlist_dir, exist_ok=True)
        
        # Update progress with initial state
        progress_dict[task_id] = DownloadProgress(
            status="queued",
            progress=0,
            file_path=playlist_dir  # Directory where files will be saved
        )
        
        items = playlist_info['items']
        total_items = len(items)
        
        # Filter items if specific indices are requested
        if selected_indices:
            # Filter to only include the selected indices (adjust for 0-based index)
            items = [items[i] for i in selected_indices if 0 <= i < total_items]
        
        # Count of successfully downloaded items
        success_count = 0
        failed_items = []
        
        # Download each item
        for index, item in enumerate(items):
            # Prefix text for the current item
            prefix_text = f"Downloading '{item['title']}' ({index+1} of {len(items)})..."
            
            # Update progress status with current item (initial state)
            progress_dict[task_id] = DownloadProgress(
                status="downloading",
                progress=0,
                file_path=playlist_dir,
                status_text=f"Starting '{item['title']}' ({index+1} of {len(items)})..."
            )
            
            # Create a safe filename from the title
            safe_title = ''.join(c if c.isalnum() or c in ' -_' else '_' for c in item['title'])
            output_path = os.path.join(playlist_dir, f"{index+1:03d}_{safe_title}")
            
            try:
                # Create item-specific options
                if format_type == "video":
                    # Video download
                    quality_map = {"480p": 480, "720p": 720, "1080p": 1080, "4K": 2160}
                    height = quality_map.get(quality, 720)
                    
                    ydl_opts = {
                        'format': f'best[height<={height}]/bestvideo[height<={height}]+bestaudio/best',
                        'outtmpl': f"{output_path}.{file_format}",
                        'progress_hooks': [ProgressHook(task_id, progress_dict, custom_status_prefix=prefix_text)],
                        'quiet': False,
                        'retries': 5,
                        'ffmpeg_location': get_ffmpeg_path(),
                        'concurrent_fragment_downloads': 10,
                        'http_chunk_size': 10485760,
                        'extractor_args': {'youtube': ['player_client=android', 'player_client=web']},
                        'fragment_retries': 5,
                        'skip_unavailable_fragments': True,
                    }
                else:
                    # Audio download
                    quality_map = {"128k": 128, "256k": 256, "320k": 320}
                    bitrate = quality_map.get(quality, 128)
                    
                    ydl_opts = {
                        'format': 'bestaudio/best',
                        'outtmpl': output_path,
                        'progress_hooks': [ProgressHook(task_id, progress_dict, custom_status_prefix=prefix_text)],
                        'postprocessors': [{
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': file_format,
                            'preferredquality': str(bitrate),
                        }],
                        'quiet': False,
                        'retries': 5,
                        'ffmpeg_location': get_ffmpeg_path(),
                        'concurrent_fragment_downloads': 10,
                        'http_chunk_size': 10485760,
                        'extractor_args': {'youtube': ['player_client=android', 'player_client=web']},
                        'fragment_retries': 5,
                        'skip_unavailable_fragments': True,
                    }
                
                # Use the video URL if available, otherwise construct from ID
                video_url = f"https://www.youtube.com/watch?v={item['id']}"
                
                # Download the item
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([video_url])
                
                success_count += 1
                
            except Exception as e:
                # Record the failure but continue with other items
                failed_items.append({
                    "title": item['title'],
                    "error": str(e)
                })
        
        # Create a report file with download results
        report_path = os.path.join(playlist_dir, "download_report.txt")
        with open(report_path, "w") as f:
            f.write(f"Playlist: {playlist_info['playlist_title']}\n")
            f.write(f"Total items: {total_items}\n")
            f.write(f"Successfully downloaded: {success_count}\n")
            f.write(f"Failed: {len(failed_items)}\n\n")
            
            if failed_items:
                f.write("Failed items:\n")
                for item in failed_items:
                    f.write(f"- {item['title']}: {item['error']}\n")
        
        # Create a zip file of the playlist directory
        zip_path = f"{playlist_dir}.zip"
        shutil.make_archive(playlist_dir, 'zip', playlist_dir)
        
        # NEW: Clean up original directory after zipping
        try:
            shutil.rmtree(playlist_dir)
            print(f"Removed original playlist directory: {playlist_dir}")
        except Exception as e:
            print(f"Warning: Could not remove playlist directory: {e}")
        
        # Update progress
        progress_dict[task_id] = DownloadProgress(
            status="completed",
            progress=100,
            file_path=zip_path
        )
    
    except Exception as e:
        progress_dict[task_id] = DownloadProgress(
            status="error",
            progress=0,
            error=str(e)
        )

# Spotify functions have been moved to spotify_handler.py

def log_debug(msg):
    with open('debug.log', 'a') as logf:
        logf.write(msg + '\n')

def download_tiktok(url: str, format_type: str, task_id: str, progress_dict: dict):
    import urllib.request
    import json
    import os
    from config import GLOBAL_TMP_DIR
    
    progress_dict[task_id] = DownloadProgress(status="downloading", progress=10, status_text="Fetching TikTok URL...")
    try:
        req = urllib.request.Request(f"https://www.tikwm.com/api/?url={url}", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
        
        if format_type == "audio":
            media_url = data['data']['music']
            file_ext = "mp3"
        else:
            media_url = data['data']['play']
            file_ext = "mp4"
            
        final_output = os.path.join(GLOBAL_TMP_DIR, f"tiktok_{task_id}.{file_ext}")
        progress_dict[task_id] = DownloadProgress(status="downloading", progress=50, status_text="Downloading media...")
        urllib.request.urlretrieve(media_url, final_output)
        
        progress_dict[task_id] = DownloadProgress(status="completed", progress=100, file_path=final_output)
    except Exception as e:
        progress_dict[task_id] = DownloadProgress(status="error", progress=0, error=str(e))
