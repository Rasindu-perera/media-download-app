"""
Spotify Web API Integration Module (Scraper Fallback)
Replaces the official API to bypass Premium restrictions.
"""

import urllib.request
from html.parser import HTMLParser
import re

class SpotifyMetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.metas = []
        
    def handle_starttag(self, tag, attrs):
        if tag == 'meta':
            self.metas.append(dict(attrs))

def scrape_spotify_html(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'})
    try:
        with urllib.request.urlopen(req) as response:
            html = response.read().decode('utf-8')
            parser = SpotifyMetaParser()
            parser.feed(html)
            return parser.metas
    except Exception as e:
        print(f"Error scraping {url}: {e}")
        return []

def extract_spotify_id_from_url(url: str) -> tuple:
    if "track" in url:
        return "track", url.split("/track/")[1].split("?")[0]
    elif "playlist" in url:
        return "playlist", url.split("/playlist/")[1].split("?")[0]
    elif "album" in url:
        return "album", url.split("/album/")[1].split("?")[0]
    return None, None

def get_track_info(track_id: str) -> dict:
    url = f"https://open.spotify.com/track/{track_id}"
    metas = scrape_spotify_html(url)
    
    info = {
        'id': track_id,
        'title': f'Spotify Track {track_id[:8]}',
        'artist': 'Unknown Artist',
        'album': 'Unknown Album',
        'duration': 180,
        'release_date': None,
        'album_art': None
    }
    
    for m in metas:
        prop = m.get('property', m.get('name', ''))
        content = m.get('content', '')
        if prop == 'og:title':
            info['title'] = content
        elif prop == 'og:description':
            parts = [p.strip() for p in content.split(' · ')]
            if len(parts) >= 1: info['artist'] = parts[0]
            if len(parts) >= 2: info['album'] = parts[1]
        elif prop == 'music:duration':
            try: info['duration'] = int(content)
            except: pass
        elif prop == 'og:image':
            info['album_art'] = content
        elif prop == 'music:release_date':
            info['release_date'] = content
            
    return info

def get_playlist_info(playlist_id: str) -> dict:
    url = f"https://open.spotify.com/playlist/{playlist_id}"
    metas = scrape_spotify_html(url)
    
    info = {
        'id': playlist_id,
        'name': f'Spotify Playlist {playlist_id[:8]}',
        'description': '',
        'owner': 'Spotify',
        'total_tracks': 0,
        'image': None,
        'tracks': []
    }
    
    track_urls = []
    
    for m in metas:
        prop = m.get('property', m.get('name', ''))
        content = m.get('content', '')
        if prop == 'og:title':
            info['name'] = content
        elif prop == 'description':
            info['description'] = content
        elif prop == 'og:image':
            info['image'] = content
        elif prop == 'music:song':
            track_urls.append(content)
            
    info['total_tracks'] = len(track_urls)
    
    # Process each track url
    for t_url in track_urls:
        t_id = extract_spotify_id_from_url(t_url)[1]
        if t_id:
            info['tracks'].append(get_track_info(t_id))
            
    return info

def get_spotify_content_info(url: str) -> dict:
    content_type, content_id = extract_spotify_id_from_url(url)
    if not content_type or not content_id:
        raise ValueError("Invalid Spotify URL")
        
    if content_type == 'track':
        track = get_track_info(content_id)
        return {
            'is_playlist': False,
            'platform': 'spotify',
            'items': [track],
            'count': 1
        }
    elif content_type in ['playlist', 'album']:
        playlist = get_playlist_info(content_id)
        return {
            'is_playlist': True,
            'platform': 'spotify',
            'playlist_title': playlist['name'],
            'items': playlist['tracks'],
            'count': len(playlist['tracks'])
        }

if __name__ == "__main__":
    print(get_spotify_content_info('https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT'))
    pl = get_spotify_content_info('https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M')
    print(f"Playlist {pl['playlist_title']} has {pl['count']} tracks")
    print(f"First track: {pl['items'][0]['title']} by {pl['items'][0]['artist']}")
