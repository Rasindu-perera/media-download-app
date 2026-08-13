# Media Download Application

A comprehensive media downloader that supports YouTube, Facebook, Instagram, TikTok, and Spotify, with options to download videos and audio in various qualities and formats.

## Features

- **Native Desktop Application**: Packaged as a clean, standalone desktop window using PyWebView!
- **Zero Configuration Downloads**: Files are automatically saved to your OS native `Downloads` folder, utilizing hidden background OS temp folders to keep your directories clean.
- **Multi-platform support**: Download from YouTube, Facebook, Instagram, TikTok and Spotify*
- **Video download options**:
  - Multiple resolutions: 480p, 720p, 1080p, 4K
  - Format options: MP4, WebM
- **Audio extraction**:
  - Multiple quality options: 128k, 256k, 320k
  - Format options: MP3, M4A, Opus
- **Playlist support**:
  - YouTube playlists with selective video download
  - Spotify playlists with selective track download
- **Metadata tagging** for Spotify downloads
- **Real-time progress tracking**
- **Clean, responsive user interface**

## Technology Stack

### Backend
- **Language**: Python
- **Framework**: FastAPI (runs as a daemon thread)
- **Desktop Windowing**: PyWebView
- **Media Tool**: yt-dlp & Cobalt API
- **Format Conversion**: FFmpeg (integrated with yt-dlp)

### Frontend
- **Framework**: React.js
- **Styling**: Bootstrap

## Installation

### Prerequisites
- Python 3.8+
- Node.js 14+
- FFmpeg

### Setup & Run

1. Clone or download the repository
   ```bash
   git clone https://github.com/Rasindu-perera/media-download-app.git
   cd media-download-app
   ```

2. Build the React Frontend (One-time setup)
   ```bash
   cd frontend
   npm install
   npm run build
   ```

3. Set up Python backend dependencies
   ```bash
   cd ../backend
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On Mac/Linux:
   source venv/bin/activate
   
   pip install -r requirements.txt
   pip install -U yt-dlp
   ```

4. Launch the Desktop App
   ```bash
   python main.py
   ```
   *The application will automatically launch in a native desktop window.*

### Bundling into a `.exe`
Because the app uses PyWebView and FastAPI, you can use PyInstaller to package the entire app into a single `.exe` file!
```bash
cd backend
pyinstaller --noconsole --add-data "../frontend/build;frontend/build" main.py
```
*Note: Make sure to adjust paths and imports based on your specific pyinstaller configuration.*

## Usage

1. Launch the application
2. Paste a URL from YouTube, Facebook, Instagram, TikTok, or Spotify
3. Click "Check Formats" to analyze available options
4. Choose between video or audio download (Spotify links automatically use audio-only mode)
5. Select your desired quality and format
6. Click "Download" and wait for the process to complete
7. The downloaded file will automatically appear in your computer's standard `Downloads` folder!

### Playlist Downloads

1. For YouTube or Spotify playlists, paste the playlist URL and click "Check Formats"
2. Select which tracks/videos you want to download from the playlist
3. Choose your desired quality and format
4. Click "Download" and wait for the process to complete
5. Your playlist will be downloaded and packaged as a ZIP file directly into your `Downloads` folder.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) for the powerful download engine
- [FastAPI](https://fastapi.tiangolo.com/) for the efficient backend
- [React](https://reactjs.org/) for the frontend framework
- [PyWebView](https://pywebview.flowrl.com/) for the native desktop integration

\* *Note: Due to Spotify's DRM protection, direct downloads are not possible. For Spotify URLs, the app will search for similar tracks on YouTube as an alternative source. This is provided for educational purposes only.*