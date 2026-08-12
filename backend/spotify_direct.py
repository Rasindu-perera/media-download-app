import urllib.request
import json
from youtubesearchpython import VideosSearch
from pytubefix import YouTube

def get_spotify_stream_url(spotify_url: str) -> str:
    """
    3-step process to get direct audio stream from a Spotify URL.
    1. Fetch metadata via oEmbed
    2. Search YouTube via youtube-search-python
    3. Extract audio URL via pytubefix
    """
    # 1. Metadata Fetching (oEmbed API)
    oembed_url = f"https://open.spotify.com/oembed?url={spotify_url}"
    req = urllib.request.Request(oembed_url, headers={'User-Agent': 'Mozilla/5.0'})
    
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode('utf-8'))
        title = data.get("title")
        
    if not title:
        raise ValueError("Could not extract title from Spotify oEmbed API.")

    # 2. YouTube Matching
    videos_search = VideosSearch(title, limit=1)
    results = videos_search.result()
    
    if not results or not results.get("result"):
        raise ValueError("No matching YouTube video found for this title.")
        
    yt_url = results["result"][0]["link"]

    # 3. Audio Extraction (pytubefix)
    yt = YouTube(yt_url, client='TV')
    
    # Filter for audio-only streams and get the best one
    audio_stream = yt.streams.get_audio_only()
    
    if not audio_stream:
        raise ValueError("Could not extract audio stream from the YouTube video.")
        
    return audio_stream.url
