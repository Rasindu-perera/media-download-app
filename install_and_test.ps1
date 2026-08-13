# Script to install required packages and test Spotify functionality

Write-Host "Building React frontend..."
cd $PSScriptRoot\frontend
npm install
npm run build

Write-Host "Installing backend dependencies..."
cd $PSScriptRoot\backend
python -m pip install -r requirements.txt
python -m pip install -U yt-dlp

Write-Host "`nStarting the Native Desktop Application (press Ctrl+C in this console to stop when done testing)..."
Write-Host "`nTEST URLS:"
Write-Host "YouTube: https://www.youtube.com/watch?v=dQw4w9WgXcQ"
Write-Host "YouTube Playlist: https://www.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"

Write-Host "`n[NOTE ABOUT SPOTIFY]"
Write-Host "Due to Spotify's DRM protection, direct downloads are not possible."
Write-Host "The app will search for similar tracks on YouTube as an alternative."
Write-Host "This is a workaround for educational purposes only."
Write-Host "Spotify track: https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
Write-Host "Spotify playlist: https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"

python main.py
