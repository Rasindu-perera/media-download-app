import urllib.request
import re
import json

try:
    req = urllib.request.Request('https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M', headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        html = response.read().decode('utf-8')
        print('Length:', len(html))
        
        # Try finding session data
        match = re.search(r'<script id="session" data-testid="session" type="application/json">(.*?)</script>', html)
        if match:
            print('Found session')
            print(json.loads(match.group(1)).keys())
            
        # See if there's any JSON with 'accessToken'
        for m in re.finditer(r'<script.*?type="application/json">(.*?)</script>', html):
            try:
                data = json.loads(m.group(1))
                if isinstance(data, dict) and 'accessToken' in data:
                    print('Found in data keys!', data.keys())
                    print('Token:', data['accessToken'][:10])
            except:
                pass
except Exception as e:
    print('Error:', e)
