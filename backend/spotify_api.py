"""
Spotify Web Scraping Module

This module replaces the authenticated Spotify API with a web scraper that extracts
the JSON data directly from Spotify's public embed widgets.
This bypasses the need for Developer API credentials and Premium accounts.
"""

import re
import json
import requests
from typing import Dict, Tuple

def get_headers() -> Dict[str, str]:
    """Get headers for Spotify requests."""
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    }

def extract_spotify_id_from_url(url: str) -> tuple:
    """
    Extract the type of content and ID from a Spotify URL.
    """
    if "track" in url:
        content_type = "track"
        track_id = url.split("/track/")[1].split("?")[0]
        return content_type, track_id
    elif "playlist" in url:
        content_type = "playlist"
        playlist_id = url.split("/playlist/")[1].split("?")[0]
        return content_type, playlist_id
    elif "album" in url:
        content_type = "album"
        album_id = url.split("/album/")[1].split("?")[0]
        return content_type, album_id
    else:
        return None, None

def scrape_spotify_embed(content_type: str, content_id: str) -> Dict:
    """
    Scrape the Spotify embed widget for a track, playlist, or album to get its metadata.
    """
    url = f"https://open.spotify.com/embed/{content_type}/{content_id}"
    response = requests.get(url, headers=get_headers())
    
    if response.status_code != 200:
        raise Exception(f"Failed to fetch Spotify embed page: {response.status_code}")
        
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', response.text, re.DOTALL)
    if not match:
        raise Exception("Could not find metadata inside Spotify embed page")
        
    data = json.loads(match.group(1))
    
    try:
        entity = data['props']['pageProps']['state']['data']['entity']
    except KeyError:
        raise Exception("Unexpected JSON structure in Spotify embed page")
        
    return entity

def get_spotify_content_info(url: str) -> Dict:
    """
    Get information about any Spotify content by scraping the embed widget.
    Returns format expected by the frontend: { "type": "playlist", "items": [...] }
    """
    content_type, content_id = extract_spotify_id_from_url(url)
    if not content_type or not content_id:
        raise ValueError("Invalid Spotify URL")
        
    entity = scrape_spotify_embed(content_type, content_id)
    
    title = entity.get('title', 'Unknown')
    subtitle = entity.get('subtitle', 'Unknown')
    
    # Extract cover art safely
    cover_art_url = None
    try:
        cover_art_url = entity.get('coverArt', {}).get('sources', [{}])[0].get('url')
    except (IndexError, AttributeError):
        pass

    if content_type == "track":
        track_info = {
            "id": content_id,
            "title": title,
            "artist": subtitle,
            "album": "Unknown Album",
            "duration": int(entity.get('duration', 0) / 1000),
            "thumbnail": cover_art_url
        }
        return {
            "type": "single",
            "data": track_info
        }
    else:
        # It's a playlist or album
        track_list = entity.get('trackList', [])
        items = []
        for track in track_list:
            track_cover = None
            try:
                track_cover = track.get('coverArt', {}).get('sources', [{}])[0].get('url')
            except (IndexError, AttributeError):
                track_cover = cover_art_url # Fallback to playlist cover art
                
            items.append({
                "id": track.get('id', ''),
                "title": track.get('title', 'Unknown Title'),
                "artist": track.get('subtitle', 'Unknown Artist'),
                "album": "Unknown Album",
                "duration": int(track.get('duration', 0) / 1000),
                "thumbnail": track_cover
            })
            
        return {
            "type": "playlist",
            "playlist_title": title,
            "items": items,
            "count": len(items)
        }
