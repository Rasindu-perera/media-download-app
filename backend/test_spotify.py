import urllib.request
import re
import json

try:
    req = urllib.request.Request('https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M', headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        html = response.read().decode('utf-8')
        
        match = re.search(r'"accessToken":"(.*?)"', html)
        if match:
            print('Token:', match.group(1)[:10])
            
            # Now try fetching the playlist
            api_req = urllib.request.Request(
                'https://api.spotify.com/v1/playlists/37i9dQZF1DXcBWIGoYBM5M',
                headers={'Authorization': f'Bearer {match.group(1)}', 'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(api_req) as api_res:
                data = json.loads(api_res.read().decode('utf-8'))
                print('Success! Name:', data['name'])
        else:
            print('No token found')
except Exception as e:
    print('Error:', e)
