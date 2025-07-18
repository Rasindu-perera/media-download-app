import os
import shutil
import re
from typing import Dict, Optional
from urllib.parse import urlparse

def clean_filename(filename: str) -> str:
    """Clean a filename by removing invalid characters."""
    # Replace invalid characters with underscore
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", filename)
    # Limit length
    return cleaned[:100] if len(cleaned) > 100 else cleaned

def get_platform(url: str) -> Optional[str]:
    """Determine the platform from the URL."""
    parsed_url = urlparse(url)
    domain = parsed_url.netloc.lower()
    
    if 'youtube.com' in domain or 'youtu.be' in domain:
        return 'youtube'
    elif 'facebook.com' in domain or 'fb.com' in domain or 'fb.watch' in domain:
        return 'facebook'
    elif 'instagram.com' in domain:
        return 'instagram'
    elif 'tiktok.com' in domain:
        return 'tiktok'
    elif 'spotify.com' in domain or 'open.spotify.com' in domain:
        return 'spotify'
    
    return None

def get_temp_path(filename: str = None) -> str:
    """Get path to temporary directory, creating it if needed."""
    temp_dir = os.path.join(os.getcwd(), "tmp")
    os.makedirs(temp_dir, exist_ok=True)
    
    if filename:
        return os.path.join(temp_dir, filename)
    return temp_dir

def clear_temp_files(older_than_hours: int = 24) -> int:
    """
    Clear temporary files older than specified hours.
    Returns number of files deleted.
    """
    import time
    
    temp_dir = get_temp_path()
    current_time = time.time()
    older_than_seconds = older_than_hours * 3600
    
    count = 0
    for filename in os.listdir(temp_dir):
        file_path = os.path.join(temp_dir, filename)
        
        # Skip directories
        if os.path.isdir(file_path):
            continue
            
        # Check file age
        file_age = current_time - os.path.getmtime(file_path)
        if file_age > older_than_seconds:
            try:
                os.remove(file_path)
                count += 1
            except Exception as e:
                print(f"Error deleting {file_path}: {e}")
    
    return count

def format_filesize(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    if not size_bytes:
        return "Unknown size"
        
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024 or unit == 'GB':
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024

def extract_spotify_id(url: str) -> Optional[str]:
    """Extract Spotify track or playlist ID from URL."""
    # Examples:
    # https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT
    # https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M

    patterns = [
        r'spotify\.com/track/([a-zA-Z0-9]+)',
        r'spotify\.com/playlist/([a-zA-Z0-9]+)',
        r'spotify\.com/album/([a-zA-Z0-9]+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None

def format_spotify_filename(artist: str, title: str, index: Optional[int] = None) -> str:
    """Create a clean, formatted filename for Spotify tracks."""
    # Clean the artist and title
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