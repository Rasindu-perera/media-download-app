"""
Spotify Web API Integration Module

This module provides functions to interact with the Spotify Web API to get actual
track and playlist information, improving the accuracy of our downloads.
"""

import os
import base64
import json
import time
from typing import Dict, List, Optional
import requests

# Spotify API credentials
CLIENT_ID = "fdb8b3417e1d4001ada0c42160d50632"
CLIENT_SECRET = "eeaeb5d0c42445ea8d347f91e3ea0b89"

# Token storage
_access_token = None
_token_expiry = 0


def get_spotify_token() -> str:
    """
    Get a valid Spotify access token, refreshing if necessary.
    """
    global _access_token, _token_expiry
    
    # Check if we need a new token
    current_time = time.time()
    if _access_token is None or current_time >= _token_expiry:
        # Prepare for token request
        auth_string = f"{CLIENT_ID}:{CLIENT_SECRET}"
        auth_bytes = auth_string.encode('utf-8')
        auth_base64 = str(base64.b64encode(auth_bytes), 'utf-8')
        
        url = "https://accounts.spotify.com/api/token"
        headers = {
            "Authorization": f"Basic {auth_base64}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = {"grant_type": "client_credentials"}
        
        # Make the request
        response = requests.post(url, headers=headers, data=data)
        
        if response.status_code != 200:
            raise Exception(f"Failed to get Spotify token: {response.text}")
        
        # Parse the response
        response_data = response.json()
        _access_token = response_data["access_token"]
        # Set expiry time (subtract 60 seconds for safety margin)
        expires_in = response_data.get("expires_in", 3600)  # Default to 1 hour
        _token_expiry = current_time + expires_in - 60
    
    return _access_token


def get_headers() -> Dict[str, str]:
    """Get headers for Spotify API requests."""
    token = get_spotify_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }


def extract_spotify_id_from_url(url: str) -> tuple:
    """
    Extract the type of content and ID from a Spotify URL.
    
    Args:
        url: Spotify URL
        
    Returns:
        Tuple of (content_type, content_id)
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


def get_track_info(track_id: str) -> Dict:
    """
    Get detailed information about a Spotify track.
    
    Args:
        track_id: Spotify track ID
        
    Returns:
        Dictionary with track information
    """
    url = f"https://api.spotify.com/v1/tracks/{track_id}"
    headers = get_headers()
    
    response = requests.get(url, headers=headers)
    
    if response.status_code != 200:
        raise Exception(f"Failed to get track info: {response.text}")
    
    track_data = response.json()
    
    # Extract relevant information
    track_info = {
        "id": track_data["id"],
        "title": track_data["name"],
        "artist": track_data["artists"][0]["name"],  # Primary artist
        "album": track_data["album"]["name"],
        "duration": int(track_data["duration_ms"] / 1000),  # Convert to seconds
        "release_date": track_data["album"]["release_date"],
        "album_art": track_data["album"]["images"][0]["url"] if track_data["album"]["images"] else None
    }
    
    # Get multiple artists if available
    if len(track_data["artists"]) > 1:
        all_artists = [artist["name"] for artist in track_data["artists"]]
        track_info["all_artists"] = all_artists
        # Format multiple artists for display
        track_info["artist"] = ", ".join(all_artists)
    
    return track_info


def get_playlist_info(playlist_id: str) -> Dict:
    """
    Get detailed information about a Spotify playlist.
    
    Args:
        playlist_id: Spotify playlist ID
        
    Returns:
        Dictionary with playlist information and tracks
    """
    # First get playlist details
    playlist_url = f"https://api.spotify.com/v1/playlists/{playlist_id}"
    headers = get_headers()
    
    response = requests.get(playlist_url, headers=headers)
    
    if response.status_code != 200:
        raise Exception(f"Failed to get playlist info: {response.text}")
    
    playlist_data = response.json()
    
    # Basic playlist info
    playlist_info = {
        "id": playlist_data["id"],
        "name": playlist_data["name"],
        "description": playlist_data["description"],
        "owner": playlist_data["owner"]["display_name"],
        "total_tracks": playlist_data["tracks"]["total"],
        "image": playlist_data["images"][0]["url"] if playlist_data["images"] else None
    }
    
    # Get all tracks (handles pagination)
    tracks = []
    tracks_url = playlist_data["tracks"]["href"]
    
    while tracks_url:
        tracks_response = requests.get(tracks_url, headers=headers)
        
        if tracks_response.status_code != 200:
            raise Exception(f"Failed to get playlist tracks: {tracks_response.text}")
        
        tracks_data = tracks_response.json()
        
        # Process tracks in this page
        for item in tracks_data["items"]:
            if item["track"]:  # Some items might be None if tracks were removed
                track = item["track"]
                track_info = {
                    "id": track["id"],
                    "title": track["name"],
                    "artist": track["artists"][0]["name"],  # Primary artist
                    "album": track["album"]["name"],
                    "duration": int(track["duration_ms"] / 1000),  # Convert to seconds
                    "album_art": track["album"]["images"][0]["url"] if track["album"]["images"] else None
                }
                
                # Get multiple artists if available
                if len(track["artists"]) > 1:
                    all_artists = [artist["name"] for artist in track["artists"]]
                    track_info["all_artists"] = all_artists
                    # Format multiple artists for display
                    track_info["artist"] = ", ".join(all_artists)
                
                tracks.append(track_info)
        
        # Get next page if available
        tracks_url = tracks_data.get("next")
    
    # Complete playlist information with tracks
    playlist_info["tracks"] = tracks
    
    return playlist_info


def get_album_info(album_id: str) -> Dict:
    """
    Get detailed information about a Spotify album.
    
    Args:
        album_id: Spotify album ID
        
    Returns:
        Dictionary with album information and tracks
    """
    # First get album details
    album_url = f"https://api.spotify.com/v1/albums/{album_id}"
    headers = get_headers()
    
    response = requests.get(album_url, headers=headers)
    
    if response.status_code != 200:
        raise Exception(f"Failed to get album info: {response.text}")
    
    album_data = response.json()
    
    # Basic album info
    album_info = {
        "id": album_data["id"],
        "name": album_data["name"],
        "artist": album_data["artists"][0]["name"],  # Primary artist
        "release_date": album_data["release_date"],
        "total_tracks": album_data["total_tracks"],
        "image": album_data["images"][0]["url"] if album_data["images"] else None
    }
    
    # Get multiple artists if available
    if len(album_data["artists"]) > 1:
        all_artists = [artist["name"] for artist in album_data["artists"]]
        album_info["all_artists"] = all_artists
        # Format multiple artists for display
        album_info["artist"] = ", ".join(all_artists)
    
    # Get all tracks (handles pagination)
    tracks = []
    tracks_url = album_data["tracks"]["href"]
    
    while tracks_url:
        tracks_response = requests.get(tracks_url, headers=headers)
        
        if tracks_response.status_code != 200:
            raise Exception(f"Failed to get album tracks: {tracks_response.text}")
        
        tracks_data = tracks_response.json()
        
        # Process tracks in this page
        for track in tracks_data["items"]:
            track_info = {
                "id": track["id"],
                "title": track["name"],
                "artist": track["artists"][0]["name"],  # Primary artist
                "album": album_info["name"],  # Use album name from parent
                "duration": int(track["duration_ms"] / 1000),  # Convert to seconds
                "album_art": album_info["image"]  # Use album art from parent
            }
            
            # Get multiple artists if available
            if len(track["artists"]) > 1:
                all_artists = [artist["name"] for artist in track["artists"]]
                track_info["all_artists"] = all_artists
                # Format multiple artists for display
                track_info["artist"] = ", ".join(all_artists)
            
            tracks.append(track_info)
        
        # Get next page if available
        tracks_url = tracks_data.get("next")
    
    # Complete album information with tracks
    album_info["tracks"] = tracks
    
    return album_info


def get_spotify_content_info(url: str) -> Dict:
    """
    Get information about any Spotify content (track, playlist, or album).
    
    Args:
        url: Spotify URL
        
    Returns:
        Dictionary with information about the content
    """
    content_type, content_id = extract_spotify_id_from_url(url)
    
    if not content_type or not content_id:
        raise ValueError("Invalid Spotify URL")
    
    if content_type == "track":
        track_info = get_track_info(content_id)
        return {
            "is_playlist": False,
            "platform": "spotify",
            "items": [track_info],
            "count": 1
        }
    elif content_type == "playlist":
        playlist_info = get_playlist_info(content_id)
        return {
            "is_playlist": True,
            "platform": "spotify",
            "playlist_title": playlist_info["name"],
            "description": playlist_info["description"],
            "owner": playlist_info["owner"],
            "image": playlist_info["image"],
            "items": playlist_info["tracks"],
            "count": playlist_info["total_tracks"]
        }
    elif content_type == "album":
        album_info = get_album_info(content_id)
        return {
            "is_playlist": True,  # Treat albums like playlists
            "platform": "spotify",
            "playlist_title": f"{album_info['name']} (Album)",
            "description": f"Album by {album_info['artist']}",
            "owner": album_info["artist"],
            "image": album_info["image"],
            "items": album_info["tracks"],
            "count": album_info["total_tracks"]
        }
    else:
        raise ValueError(f"Unsupported Spotify content type: {content_type}")


def download_album_art(image_url: str, output_path: str) -> str:
    """
    Download album art from Spotify.
    
    Args:
        image_url: URL of the album art
        output_path: Path to save the image
        
    Returns:
        Path to the saved image
    """
    if not image_url:
        return None
    
    response = requests.get(image_url)
    
    if response.status_code != 200:
        print(f"Failed to download album art: {response.status_code}")
        return None
    
    with open(output_path, "wb") as f:
        f.write(response.content)
    
    return output_path
