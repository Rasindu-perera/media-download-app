import urllib.request
import re
import json
import base64

try:
    req = urllib.request.Request('https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M', headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        html = response.read().decode('utf-8')
        
        match = re.search(r'<script id="initial-state" type="text/plain">(.*?)</script>', html)
        if match:
            state_json = base64.b64decode(match.group(1)).decode('utf-8')
            with open('spotify_state.json', 'w', encoding='utf-8') as f:
                f.write(state_json)
            print('Found initial-state! Wrote to spotify_state.json')
            
            # Now let's try to parse the state!
            state = json.loads(state_json)
            print("Keys:", state.keys())
except Exception as e:
    print('Error:', e)
