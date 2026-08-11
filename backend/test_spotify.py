import asyncio
import sys
import traceback
sys.path.append('.')
from spotify_handler import download_spotify_playlist
from downloader import DownloadProgress

async def main():
    progress = {}
    try:
        await download_spotify_playlist('https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M', '320k', 'mp3', 'test_uuid_123', progress, [0])
    except Exception as e:
        traceback.print_exc()
    print("Final progress dict:", progress)

asyncio.run(main())
