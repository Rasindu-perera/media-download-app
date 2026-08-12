import urllib.request
from bs4 import BeautifulSoup
import re
import json

try:
    req = urllib.request.Request('https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M', headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        html = response.read().decode('utf-8')
        
        # Spotify puts state in a script tag with id 'initial-state'
        # Base64 encoded JSON!
        match = re.search(r'<script id="initial-state" type="text/plain">(.*?)</script>', html)
        if match:
            import base64
            state_json = base64.b64decode(match.group(1)).decode('utf-8')
            state = json.loads(state_json)
            print("Found initial-state!")
            # Save it to a file so we can inspect it
            with open('spotify_state.json', 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
            print("Saved state to spotify_state.json")
except Exception as e:
    print('Error:', e)
