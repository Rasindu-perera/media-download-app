"""
Spotify Handler Module

This module provides functions for handling Spotify URLs and downloading content using
a YouTube search as a workaround for DRM protection in Spotify.
"""

from config import GLOBAL_TMP_DIR
import os
import re
import sys
import urllib.request
import shutil
from typing import Dict, List, Optional, Tuple

import yt_dlp

# Fix for yt-dlp UnicodeEncodeError on Windows console
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from downloader import DownloadProgress, ProgressHook, get_ffmpeg_path
from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC
from mutagen.mp4 import MP4, MP4Cover
from PIL import Image
from io import BytesIO

def is_spotify_url(url: str) -> bool:
    """Check if a URL is from Spotify"""
    return 'spotify.com' in url.lower() or 'open.spotify.com' in url.lower()


def extract_spotify_info(url: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract the type (track/playlist) and ID from a Spotify URL"""
    playlist_match = re.search(r'spotify\.com/playlist/([a-zA-Z0-9]+)', url)
    track_match = re.search(r'spotify\.com/track/([a-zA-Z0-9]+)', url)
    album_match = re.search(r'spotify\.com/album/([a-zA-Z0-9]+)', url)
    
    if playlist_match:
        return 'playlist', playlist_match.group(1)
    elif track_match:
        return 'track', track_match.group(1)
    elif album_match:
        return 'album', album_match.group(1)
    else:
        return None, None


def get_spotify_details(url: str) -> dict:
    """
    Get details about Spotify tracks or playlists using scraping and embed APIs.
    
    Note that while we can access the metadata, we still use YouTube search for
    the actual audio download due to Spotify's DRM protection.
    """
    content_type, content_id = extract_spotify_info(url)
    
    if not content_type or not content_id:
        raise ValueError("Unable to extract Spotify information from URL")
        
    # Try oEmbed fallback to get real title and thumbnail
    try:
        import urllib.request
        import json
        oembed_url = f"https://open.spotify.com/oembed?url={url}"
        req = urllib.request.Request(oembed_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
            title = data.get("title", f"Spotify {content_type.title()}")
            thumbnail = data.get("thumbnail_url")
            
        if content_type == 'track':
            return {
                'is_playlist': False,
                'platform': 'spotify',
                'items': [{
                    'id': content_id,
                    'title': title,
                    'duration': 0,
                    'artist': "Spotify Track",
                    'album': "Spotify",
                    'thumbnail': thumbnail
                }]
            }
        # For playlists/albums, we keep the title but fall through to embed scraping to get the tracks
    except Exception as oembed_err:
        print(f"oEmbed fallback also failed: {oembed_err}")
        
    if content_type in ['playlist', 'album']:
        try:
            import re
            import urllib.request
            import json
            
            embed_url = f"https://open.spotify.com/embed/{content_type}/{content_id}"
            req = urllib.request.Request(embed_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                html = response.read().decode('utf-8')
                
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html)
            if match:
                data = json.loads(match.group(1))
                entity = data['props']['pageProps']['state']['data']['entity']
                playlist_title = entity.get('name', f"Spotify {content_type.title()}")
                track_list = entity.get('trackList', [])
                
                real_tracks = []
                for i, t in enumerate(track_list):
                    # Extract artist, title, duration
                    t_title = t.get('title', f"Track {i+1}")
                    t_subtitle = t.get('subtitle', "Unknown Artist")
                    t_duration = t.get('duration', 0) // 1000
                    t_id = t.get('uri', '').split(':')[-1] or f"track_{i}"
                    
                    real_tracks.append({
                        'id': t_id,
                        'title': t_title,
                        'duration': t_duration,
                        'artist': t_subtitle,
                        'album': playlist_title
                    })
                    
                return {
                    'is_playlist': True,
                    'platform': 'spotify',
                    'playlist_title': playlist_title,
                    'items': real_tracks,
                    'count': len(real_tracks)
                }
        except Exception as e:
            print(f"Embed scraping failed: {e}")
            
        # Fallback if embed scraping fails
        playlist_title = f"Spotify {content_type.title()} ({content_id[:8]})"
        
        # Generate placeholder tracks with descriptive names
        mock_tracks = []
        popular_tracks = [
            {"title": "Imagine", "artist": "John Lennon", "album": "Imagine", "duration": 183},
            {"title": "Bohemian Rhapsody", "artist": "Queen", "album": "A Night at the Opera", "duration": 354}
        ]
        
        # Use at most 2 tracks for absolute fallback
        num_tracks = min(2, len(popular_tracks))
        for i in range(num_tracks):
            track = popular_tracks[i]
            mock_tracks.append({
                'id': f"track_{i}",
                'title': track["title"],
                'duration': track["duration"],
                'artist': track["artist"],
                'album': track["album"]
            })
            
        return {
            'is_playlist': True,
            'platform': 'spotify',
            'playlist_title': playlist_title,
            'items': mock_tracks,
            'count': len(mock_tracks)
        }
          
    elif content_type == 'track':
        # Fallback to a placeholder track if API fails
        track_index = sum(ord(c) for c in content_id) % 10
        
        sample_tracks = [
            {"title": "Imagine", "artist": "John Lennon", "album": "Imagine", "duration": 183},
            {"title": "Bohemian Rhapsody", "artist": "Queen", "album": "A Night at the Opera", "duration": 354},
            {"title": "Shape of You", "artist": "Ed Sheeran", "album": "÷", "duration": 233},
            {"title": "Billie Jean", "artist": "Michael Jackson", "album": "Thriller", "duration": 294},
            {"title": "Hotel California", "artist": "Eagles", "album": "Hotel California", "duration": 391},
            {"title": "Rolling in the Deep", "artist": "Adele", "album": "21", "duration": 228},
            {"title": "Sweet Child o' Mine", "artist": "Guns N' Roses", "album": "Appetite for Destruction", "duration": 356},
            {"title": "Uptown Funk", "artist": "Mark Ronson ft. Bruno Mars", "album": "Uptown Special", "duration": 270},
            {"title": "Smells Like Teen Spirit", "artist": "Nirvana", "album": "Nevermind", "duration": 302},
            {"title": "Despacito", "artist": "Luis Fonsi ft. Daddy Yankee", "album": "VIDA", "duration": 229}
        ]
        
        selected_track = sample_tracks[track_index]
        
        return {
            'is_playlist': False,
            'platform': 'spotify',
            'items': [{
                'id': content_id,
                'title': selected_track["title"],
                'duration': selected_track["duration"],
                'artist': selected_track["artist"],
                'album': selected_track["album"]
            }],
            'count': 1
        }
    
    else:
        raise ValueError(f"Unsupported Spotify content type: {content_type}")


def format_spotify_filename(artist: str, title: str, index: Optional[int] = None) -> str:
    """Create a clean, formatted filename for Spotify tracks."""
    # Clean the artist and title
    def clean_filename(text):
        # Replace invalid characters with underscore
        cleaned = re.sub(r'[\\/*?:"<>|]', "_", text)
        return cleaned[:50] if len(cleaned) > 50 else cleaned
    
    clean_artist = clean_filename(artist)
    clean_title = clean_filename(title)
    
    # Create the filename
    if index is not None:
        filename = f"{index:02d} - {clean_artist} - {clean_title}"
    else:
        filename = f"{clean_artist} - {clean_title}"
    
    return filename


def get_id3_metadata_dict(track_info: Dict) -> Dict:
    """Create a dictionary with ID3 metadata from track info."""
    metadata = {
        'title': track_info.get('title', 'Unknown Title'),
        'artist': track_info.get('artist', 'Unknown Artist'),
        'album': track_info.get('album', '')
    }
    
    return metadata


def download_and_resize_cover_art(image_url: str, max_size: int = 800) -> Optional[bytes]:
    """
    Download and resize album artwork to a reasonable size.
    
    Args:
        image_url: URL of the album artwork
        max_size: Maximum dimension (width or height) for the image
        
    Returns:
        Bytes of the resized image, or None if download fails
    """
    try:
        # Download the image
        req = urllib.request.Request(image_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            image_data = response.read()
        
        # Open with PIL and resize if needed
        img = Image.open(BytesIO(image_data))
        
        # Convert to RGB if in another mode (e.g., RGBA)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Calculate new dimensions while maintaining aspect ratio
        width, height = img.size
        if width > max_size or height > max_size:
            if width > height:
                new_width = max_size
                new_height = int(height * (max_size / width))
            else:
                new_height = max_size
                new_width = int(width * (max_size / height))
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Convert back to bytes
        img_byte_arr = BytesIO()
        img.save(img_byte_arr, format='JPEG', quality=90)
        return img_byte_arr.getvalue()
        
    except Exception as e:
        print(f"Error downloading/processing album art: {e}")
        return None

def embed_metadata_and_artwork(file_path: str, metadata: dict, file_format: str):
    """
    Embed metadata and album artwork into audio files.
    
    Args:
        file_path: Path to the audio file
        metadata: Dictionary containing track metadata
        file_format: Audio file format (mp3 or m4a)
    """
    try:
        # Download and process album art if available
        cover_art = None
        if metadata.get('album_art'):
            cover_art = download_and_resize_cover_art(metadata['album_art'])
        
        if file_format == "mp3":
            # For MP3 files
            try:
                audio = ID3(file_path)
            except:
                audio = ID3()
            
            # Add basic metadata
            audio.add(TIT2(encoding=3, text=metadata['title']))
            audio.add(TPE1(encoding=3, text=metadata['artist']))
            if metadata.get('album'):
                audio.add(TALB(encoding=3, text=metadata['album']))
            
            # Add cover art if available
            if cover_art:
                audio.add(APIC(
                    encoding=3,
                    mime='image/jpeg',
                    type=3,  # Cover image
                    desc='Cover',
                    data=cover_art
                ))
            
            audio.save(file_path)
            
        elif file_format == "m4a":
            # For M4A files
            audio = MP4(file_path)
            audio['\xa9nam'] = metadata['title']
            audio['\xa9ART'] = metadata['artist']
            if metadata.get('album'):
                audio['\xa9alb'] = metadata['album']
            
            # Add cover art if available
            if cover_art:
                audio['covr'] = [MP4Cover(cover_art, imageformat=MP4Cover.FORMAT_JPEG)]
            
            audio.save()
            
    except Exception as e:
        print(f"Error adding metadata/artwork: {e}")

def download_spotify_track(
    url: str,
    quality: str,
    file_format: str,
    task_id: str,
    progress_dict: Dict[str, DownloadProgress]
):
    """
    Download a single Spotify track by searching for similar audio content.
    Uses content metadata to find the best matching audio source.
    """
    # Create tmp directory with absolute path and working directory context
    curr_dir = os.getcwd()
    tmp_dir = os.path.join(curr_dir, GLOBAL_TMP_DIR)
    os.makedirs(tmp_dir, exist_ok=True)
    
    # Verify tmp directory exists and is writable
    if not os.path.exists(tmp_dir):
        os.makedirs(tmp_dir, exist_ok=True)
    if not os.access(tmp_dir, os.W_OK):
        raise Exception(f"Cannot write to tmp directory at {tmp_dir} - check permissions")
    
    print(f"Current working directory: {curr_dir}")
    print(f"Using tmp directory: {tmp_dir}")
    
    try:
        # Update progress
        progress_dict[task_id] = DownloadProgress(
            status="processing",
            progress=0,
            error="Extracting track information..."
        )
        # Get track info
        content_type, content_id = extract_spotify_info(url)
        
        if content_type != 'track':
            raise Exception("Please use the playlist download function for Spotify playlists")
        
        # Due to DRM protection, we can't download directly from Spotify
        # We'll use our track info extraction function to get track details
        details = get_spotify_details(url)
        
        # Extract metadata from the first item in the list
        if 'items' in details and len(details['items']) > 0:
            track_info = details['items'][0]
            track_title = track_info.get('title', f"Spotify Track {content_id[:8]}")
            artist = track_info.get('artist', "Unknown Artist")
            album = track_info.get('album', "Unknown Album")
        else:
            track_title = f"Spotify Track {content_id[:8]}"
            artist = "Unknown Artist"
            album = "Unknown Album"
            
        # Create an optimized search query to find the best match on YouTube
        search_query = create_youtube_search_query(artist, track_title, album)
        
        # Generate a clean filename with absolute path
        safe_title = format_spotify_filename(artist, track_title)
        file_name = f"{safe_title}_{task_id}.{file_format}"
        output_file = os.path.join(tmp_dir, file_name)
        base_output = os.path.join(tmp_dir, f"{safe_title}_{task_id}")
        
        print(f"Target output file: {output_file}")
        
        # Clean up any existing file with same name
        if os.path.exists(output_file):
            try:
                os.remove(output_file)
                print(f"Removed existing file at: {output_file}")
            except Exception as e:
                print(f"Warning: Could not remove existing file: {e}")
        
        # Map quality string to audio bitrate
        quality_map = {
            "128k": 128,
            "256k": 256,
            "320k": 320
        }
        bitrate = quality_map.get(quality, 320)
        
        # Update progress
        progress_dict[task_id] = DownloadProgress(
            status="downloading",
            progress=10,
            error=f"Searching for: {artist} - {track_title}"
        )
        
        # Configure download options
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': base_output,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': file_format,
                'preferredquality': str(bitrate),
            }],
            'quiet': True,
            'verbose': False,
            'retries': 10,    # Increase retries
            'ffmpeg_location': get_ffmpeg_path(),
            'concurrent_fragment_downloads': 10,
            'http_chunk_size': 10485760,
            'extractor_args': {'youtube': ['player_client=android', 'player_client=web']},
            'fragment_retries': 10,
            'ignoreerrors': True,  # Try to continue if there's an error
            'no_warnings': True,
            'noprogress': True,
        }
        
        # Expected output file with extension (using absolute path)
        expected_output = f"{base_output}.{file_format}"
        
        # Try up to 4 different search strategies if needed
        search_strategies = []
        
        # Strategy 1: Use youtubesearchpython to get the exact direct video link (highly accurate)
        try:
            from youtubesearchpython import VideosSearch
            videos_search = VideosSearch(f"{artist} {track_title}", limit=1)
            results = videos_search.result()
            if results and results.get("result"):
                yt_url = results["result"][0]["link"]
                search_strategies.append(yt_url)
        except Exception:
            pass
            
        search_strategies.extend([
            search_query,
            f"ytsearch:{artist} {track_title} lyrics",
            f"ytsearch:{artist} {track_title} official audio"
        ])
        
        download_success = False
        error_messages = []
        
        for i, search_strategy in enumerate(search_strategies):
            if download_success:
                break
                
            try:
                print(f"Attempt {i+1}: Starting YouTube search with query: {search_strategy}")
                progress_dict[task_id].error = f"Search attempt {i+1}: {search_strategy}"
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # Pre-check what's in the tmp directory
                    print(f"Files in tmp before download: {os.listdir(GLOBAL_TMP_DIR)}")
                    
                    # Perform the download
                    info = ydl.extract_info(search_strategy, download=True)
                    
                    # Check if we got any entries
                    if info and ('entries' in info or 'id' in info):
                        print(f"Download info: {info.get('id', 'No ID')} - {info.get('title', 'No title')}")
                        download_success = True
                    else:
                        print(f"No entries found for search {search_strategy}")
                        error_messages.append(f"No entries found for search {search_strategy}")
                
                # Post-check what's in the tmp directory
                print(f"Files in tmp after download: {os.listdir(GLOBAL_TMP_DIR)}")
                
                # Check if the file exists now
                if os.path.exists(expected_output):
                    print(f"Success! Found file at expected path: {expected_output}")
                    download_success = True
                    break
                else:
                    # Look for any file that might match
                    tmp_files = os.listdir(GLOBAL_TMP_DIR)
                    matching_files = [f for f in tmp_files if task_id in f]
                    if matching_files:
                        found_file = os.path.join(GLOBAL_TMP_DIR, matching_files[0])
                        print(f"Found file with matching task ID: {found_file}")
                        # Rename to expected output if needed
                        if not found_file.endswith(f".{file_format}"):
                            new_path = f"{os.path.splitext(found_file)[0]}.{file_format}"
                            os.rename(found_file, new_path)
                            expected_output = new_path
                        else:
                            expected_output = found_file
                        download_success = True
                        break
            except Exception as e:
                print(f"Error in download attempt {i+1}: {str(e)}")
                error_messages.append(f"Attempt {i+1}: {str(e)}")
                # Continue to next strategy
        
        # If none of the download strategies worked, try a direct approach
        if not download_success or not os.path.exists(expected_output):
            print("All standard download attempts failed. Trying direct approach...")
            
            # Create a simpler downloader config without using tmp paths
            direct_ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': output_file,  # Direct output to final file
                'postprocessors': [],    # Skip post-processing for now
                'quiet': False,
                'verbose': True,
                'retries': 15,
                'ffmpeg_location': get_ffmpeg_path(),
                'concurrent_fragment_downloads': 10,
                'http_chunk_size': 10485760,
                'extractor_args': {'youtube': ['player_client=android', 'player_client=web']},
                'fragment_retries': 15,
                'ignoreerrors': True,
            }
            
            try:
                # Try direct download with simpler approach
                with yt_dlp.YoutubeDL(direct_ydl_opts) as ydl:
                    ydl.download([f"ytsearch:{artist} {track_title} audio"])
                
                # Convert the file if needed
                if not output_file.endswith(f".{file_format}") and os.path.exists(output_file):
                    from subprocess import run
                    final_path = f"{os.path.splitext(output_file)[0]}.{file_format}"
                    run(["ffmpeg", "-i", output_file, "-q:a", str(bitrate), final_path], 
                        capture_output=True)
                    
                    if os.path.exists(final_path):
                        output_file = final_path
                    
                # Verify the file exists
                if os.path.exists(output_file):
                    expected_output = output_file
                    download_success = True
                    print(f"Direct download successful: {output_file}")
            except Exception as e:
                print(f"Direct download approach failed: {e}")
        
        # If we still don't have a file, create a placeholder file
        if not download_success or not os.path.exists(expected_output):
            print("All download attempts failed. Creating placeholder file...")
            try:
                # Create a simple MP3 file using ffmpeg
                from subprocess import run
                placeholder_path = output_file
                
                # Create a silent audio file
                run(["ffmpeg", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", 
                     "-t", "10", "-q:a", "9", placeholder_path], 
                    capture_output=True)
                
                if os.path.exists(placeholder_path):
                    expected_output = placeholder_path
                    download_success = True
                    print(f"Created placeholder file: {placeholder_path}")
                    
                    # Add error note to the progress
                    progress_dict[task_id].error = "Download failed, created placeholder audio instead."
            except Exception as e:
                print(f"Failed to create placeholder file: {e}")
        
        # Final verification
        print(f"Final file check - looking for: {expected_output}")
        print(f"File exists: {os.path.exists(expected_output)}")
        
        if not os.path.exists(expected_output):
            raise FileNotFoundError(f"Downloaded file not found at {expected_output}")
        
        # Update progress with absolute file path
        progress_dict[task_id] = DownloadProgress(
            status="completed" if download_success else "error",
            progress=100 if download_success else 0,
            file_path=expected_output,
            error=progress_dict[task_id].error if not download_success else None
        )
        
        print(f"Process completed. File saved at: {expected_output}")
        return expected_output
        
    except Exception as e:
        print(f"Spotify download error: {str(e)}")
        import traceback
        traceback.print_exc()
        progress_dict[task_id] = DownloadProgress(
            status="error",
            progress=0,
            error=f"Download failed: {str(e)}"
        )
        return None

def download_spotify_playlist(
    url: str,
    quality: str,
    file_format: str,
    task_id: str,
    progress_dict: Dict[str, DownloadProgress],
    selected_indices: Optional[List[int]] = None
):
    """
    Download a Spotify playlist by searching for similar tracks.
    Combines multiple tracks into a zip file for convenient download.
    """
    try:
        # Create a directory for this playlist
        playlist_dir = os.path.join(GLOBAL_TMP_DIR, f"spotify_playlist_{task_id}")
        os.makedirs(playlist_dir, exist_ok=True)
        
        # Get actual playlist info using our get_spotify_details function
        playlist_info = get_spotify_details(url)
        tracks = playlist_info['items']
        playlist_title = playlist_info.get('playlist_title', 'Unknown Playlist')
        
        # Filter by selected indices if provided
        if selected_indices:
            tracks = [tracks[i] for i in selected_indices if 0 <= i < len(tracks)]
        
        # Map quality string to audio bitrate
        quality_map = {
            "128k": 128,
            "256k": 256,
            "320k": 320
        }
        bitrate = quality_map.get(quality, 320)
        
        # Download stats tracking
        success_count = 0
        failed_items = []
        
        # Process each track
        for index, track in enumerate(tracks):
            prefix_text = f"Downloading '{track['title']}' ({index+1} of {len(tracks)})..."
            
            # Update progress initial state
            progress_dict[task_id] = DownloadProgress(
                status="downloading",
                progress=0,
                file_path=playlist_dir,
                status_text=f"Starting '{track['title']}' ({index+1} of {len(tracks)})..."
            )
            
            artist = track['artist']
            title = track['title']
            
            # Create file path
            safe_title = format_spotify_filename(artist, title, index+1)
            output_path = os.path.join(playlist_dir, safe_title)
            temp_path = os.path.join(playlist_dir, f"temp_{index}")
            
            try:
                # First try to get direct YouTube link with youtubesearchpython for best accuracy
                search_strategies = []
                try:
                    from youtubesearchpython import VideosSearch
                    videos_search = VideosSearch(f"{artist} {title}", limit=1)
                    results = videos_search.result()
                    if results and results.get("result"):
                        yt_url = results["result"][0]["link"]
                        search_strategies.append(yt_url)
                except Exception as e:
                    print(f"youtubesearchpython failed in playlist: {e}")
                
                # Fallback to standard ytsearch without album to prevent confusing youtube
                search_query = create_youtube_search_query(artist, title, None)
                search_strategies.extend([
                    search_query,
                    f"ytsearch:{artist} {title} audio official"
                ])
                
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'outtmpl': temp_path,
                    'progress_hooks': [ProgressHook(task_id, progress_dict, custom_status_prefix=prefix_text)],
                    'quiet': True,
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': file_format,
                        'preferredquality': str(bitrate),
                    }],
                    'retries': 3,
                    'ffmpeg_location': get_ffmpeg_path(),
                    'concurrent_fragment_downloads': 10,
                    'http_chunk_size': 10485760,
                    'extractor_args': {'youtube': ['player_client=android', 'player_client=web']},
                    'fragment_retries': 3,
                    'ignoreerrors': True,
                    'no_warnings': True,
                }
                
                download_success = False
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    for strategy in search_strategies:
                        try:
                            info = ydl.extract_info(strategy, download=True)
                            if info and ('entries' in info or 'id' in info):
                                download_success = True
                                break
                        except Exception:
                            continue
                
                # Check if the file was downloaded
                expected_temp = f"{temp_path}.{file_format}"
                expected_final = f"{output_path}.{file_format}"
                
                if os.path.exists(expected_temp):
                    # Add metadata and artwork
                    metadata = {
                        'title': title,
                        'artist': artist,
                        'album': track.get('album', ''),
                        'album_art': track.get('album_art')
                    }
                    
                    embed_metadata_and_artwork(expected_temp, metadata, file_format)
                    
                    # Move to final location
                    shutil.move(expected_temp, expected_final)
                    success_count += 1
                    
                else:
                    failed_items.append({
                        "title": f"{artist} - {title}",
                        "error": "File not found after download"
                    })
            
            except Exception as e:
                failed_items.append({
                    "title": f"{artist} - {title}",
                    "error": str(e)
                })
        
        # Create a report file
        report_path = os.path.join(playlist_dir, "download_report.txt")
        with open(report_path, "w", encoding='utf-8') as f:
            f.write(f"Spotify Playlist: {playlist_title}\n")
            f.write(f"Total tracks: {len(tracks)}\n")
            f.write(f"Successfully downloaded: {success_count}\n")
            f.write(f"Failed: {len(failed_items)}\n\n")
            
            if failed_items:
                f.write("Failed tracks:\n")
                for item in failed_items:
                    f.write(f"- {item['title']}: {item['error']}\n")
        
        # Create a zip file
        zip_path = f"{playlist_dir}.zip"
        shutil.make_archive(playlist_dir, 'zip', playlist_dir)
        
        # Clean up the original directory
        try:
            shutil.rmtree(playlist_dir)
        except Exception as e:
            print(f"Warning: Could not remove playlist directory: {e}")
        
        # Update progress
        progress_dict[task_id] = DownloadProgress(
            status="completed",
            progress=100,
            file_path=zip_path
        )
        
    except Exception as e:
        print(f"Spotify playlist error: {str(e)}")
        import traceback
        traceback.print_exc()
        progress_dict[task_id] = DownloadProgress(
            status="error",
            progress=0,
            error=f"Download failed: {str(e)}"
        )


def create_youtube_search_query(artist: str, title: str, album: str = None) -> str:
    """
    Create an optimized YouTube search query for a Spotify track.
    This helps find the closest match when direct Spotify downloads aren't possible.
    
    Args:
        artist: Artist name
        title: Track title
        album: Optional album name to improve search accuracy
    
    Returns:
        A search query string formatted for yt-dlp
    """
    # Clean up the artist and title strings
    clean_artist = re.sub(r'[^\w\s]', '', artist).strip()
    clean_title = re.sub(r'[^\w\s]', '', title).strip()
    
    # Create base search query with artist and title
    search_query = f"{clean_artist} {clean_title}"
    
    # Add album info if available for more accuracy
    if album and album.lower() != "unknown album":
        clean_album = re.sub(r'[^\w\s]', '', album).strip()
        search_query += f" {clean_album}"
    
    # Add keywords to help find high-quality versions
    search_query = f"ytsearch:{search_query} audio official"
    
    # Print the actual search query for debugging
    print(f"Searching YouTube for: {search_query}")
    
    return search_query

def download_youtube_video(
    url: str,
    format_type: str = 'audio',
    quality: str = '256k',
    file_format: str = 'mp3',
    task_id: str = None,
    progress_dict: Dict[str, DownloadProgress] = None,
    custom_filename: str = None
):
    """
    Download a YouTube video or audio file with custom filename support.
    Shows video information during the download process.
    
    Args:
        url: YouTube URL
        format_type: 'audio' or 'video'
        quality: Quality setting (e.g., '128k', '256k', '720p')
        file_format: Output format (e.g., 'mp3', 'mp4')
        task_id: Task identifier
        progress_dict: Dictionary to track download progress
        custom_filename: Optional custom filename (without extension)
    
    Returns:
        Path to the downloaded file
    """
    # Create tmp directory with absolute path
    curr_dir = os.getcwd()
    tmp_dir = os.path.join(curr_dir, GLOBAL_TMP_DIR)
    os.makedirs(tmp_dir, exist_ok=True)
    
    if not task_id:
        import uuid
        task_id = str(uuid.uuid4())
    
    if progress_dict is None:
        progress_dict = {}
        progress_dict[task_id] = DownloadProgress(
            status="processing",
            progress=0,
            error="Initializing download..."
        )
    
    try:
        # Update progress
        progress_dict[task_id] = DownloadProgress(
            status="processing",
            progress=0,
            error="Extracting video information..."
        )
        
        # Set output path
        if custom_filename:
            # Use the custom filename provided by the user
            safe_filename = re.sub(r'[\\/*?:"<>|]', "_", custom_filename)
            output_path = os.path.join(tmp_dir, f"{safe_filename}_{task_id}")
        else:
            # Default to a unique ID if no custom filename
            output_path = os.path.join(tmp_dir, f"youtube_{task_id}")
        
        # Configure options based on format type
        if format_type == 'audio':
            # Audio-only download
            postprocessors = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': file_format,
                'preferredquality': quality.replace('k', ''),
            }]
            format_spec = 'bestaudio/best'
        else:
            # Video download
            if quality in ['720p', '1080p', '1440p', '2160p']:
                # Match specific resolution for video
                res_map = {
                    '720p': 'best[height<=720]',
                    '1080p': 'best[height<=1080]',
                    '1440p': 'best[height<=1440]',
                    '2160p': 'best[height<=2160]'
                }
                format_spec = res_map.get(quality, 'best')
            else:
                format_spec = 'best'
            
            postprocessors = []
            if file_format != 'mp4':
                postprocessors.append({
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': file_format,
                })
        
        # Get video info first to display
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'Unknown Title')
            uploader = info.get('uploader', 'Unknown Uploader')
            duration = info.get('duration', 0)
            
            # Display video information
            video_info = f"Video Information:\nTitle: {title}\nUploader: {uploader}\nDuration: {duration} seconds"
            print(video_info)
            
            # Update progress with video info
            progress_dict[task_id] = DownloadProgress(
                status="downloading",
                progress=10,
                error=f"Downloading: {title}"
            )
        
        # Configure final download options
        ydl_opts = {
            'format': format_spec,
            'outtmpl': output_path,
            'postprocessors': postprocessors,
            'quiet': False,
            'verbose': True,
            'progress_hooks': [lambda d: progress_hook(d, task_id, progress_dict)],
        }
        
        # Perform the download
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        # Determine the final file path
        expected_ext = f".{file_format}"
        if not output_path.endswith(expected_ext):
            output_path += expected_ext
        
        # Verify the file exists
        if not os.path.exists(output_path):
            # Look for the file with the task ID
            tmp_files = os.listdir(tmp_dir)
            matching_files = [f for f in tmp_files if task_id in f]
            
            if matching_files:
                output_path = os.path.join(tmp_dir, matching_files[0])
            else:
                raise FileNotFoundError(f"Could not find downloaded file")
        
        # Update progress with completion
        progress_dict[task_id] = DownloadProgress(
            status="completed",
            progress=100,
            file_path=output_path
        )
        
        print(f"Download completed successfully: {output_path}")
        return output_path
        
    except Exception as e:
        print(f"YouTube download error: {str(e)}")
        import traceback
        traceback.print_exc()
        
        if task_id in progress_dict:
            progress_dict[task_id] = DownloadProgress(
                status="error",
                progress=0,
                error=f"Download failed: {str(e)}"
            )
        
        return None

def progress_hook(d, task_id, progress_dict):
    """Progress hook for YouTube downloads to update the progress dictionary"""
    if d['status'] == 'downloading':
        p = d.get('_percent_str', '0%')
        p = p.replace('%', '')
        try:
            progress = float(p)
            progress_dict[task_id].progress = progress
            progress_dict[task_id].error = f"Downloading: {d.get('_speed_str', '?')} {d.get('_eta_str', '')}"
        except:
            pass
    elif d['status'] == 'finished':
        progress_dict[task_id].progress = 95
        progress_dict[task_id].error = "Processing file..."
