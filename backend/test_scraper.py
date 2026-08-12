
import urllib.request
from html.parser import HTMLParser

class SpotifyMetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.metas = []
        
    def handle_starttag(self, tag, attrs):
        if tag == 'meta':
            self.metas.append(dict(attrs))

def scrape_track(track_id):
    req = urllib.request.Request(f'https://open.spotify.com/track/{track_id}', headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        html = response.read().decode('utf-8')
        parser = SpotifyMetaParser()
        parser.feed(html)
        
        info = {'id': track_id}
        for m in parser.metas:
            prop = m.get('property', m.get('name', ''))
            content = m.get('content', '')
            if prop == 'og:title':
                info['title'] = content
            elif prop == 'og:description':
                # e.g. Rick Astley · Whenever You Need Somebody · Song · 1987
                parts = [p.strip() for p in content.split('')]
                if len(parts) >= 1: info['artist'] = parts[0]
                if len(parts) >= 2: info['album'] = parts[1]
            elif prop == 'music:duration':
                info['duration'] = int(content)
            elif prop == 'og:image':
                info['album_art'] = content
            elif prop == 'music:release_date':
                info['release_date'] = content
        return info

print(scrape_track('4cOdK2wGLETKBW3PvgPWqT'))

