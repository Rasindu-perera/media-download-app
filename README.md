# Media Download Application

A comprehensive media downloader that supports YouTube, Facebook, Instagram, TikTok, and Spotify, with options to download videos and audio in various qualities and formats.

## Features

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
- **Framework**: FastAPI
- **Media Tool**: yt-dlp
- **Format Conversion**: FFmpeg (integrated with yt-dlp)

### Frontend
- **Framework**: React.js
- **Styling**: Bootstrap

## Installation

### Prerequisites
- Python 3.8+
- Node.js 14+
- FFmpeg

### Backend Setup

1. Set up the repository
   ```bash
   # Option 1: Clone the repository (if it contains files)
   git clone https://github.com/Rasindu-perera/media-download-app.git
   cd media-download-app
   
   # Option 2: Or create directories manually
   mkdir -p media-download-app/backend
   mkdir -p media-download-app/frontend
   cd media-download-app
   ```

2. Set up Python environment
   ```bash
   cd backend
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. Start the backend server
   ```bash
   python -m uvicorn main:app --reload #\media download app\backend>
   ```

### Frontend Setup

1. Create React application and install dependencies
   ```bash
   # Navigate to the frontend directory
   cd frontend
   
   # Initialize React application
   npx create-react-app .
   
   # Install additional dependencies
   npm install axios react-bootstrap bootstrap
   ```

2. Start the development server
   ```bash
   npm start #\media download app\frontend>
   ```

3. Open your browser and navigate to `http://localhost:3000`

## Usage

1. Paste a URL from YouTube, Facebook, Instagram, TikTok, or Spotify
2. Click "Check Formats" to analyze available options
3. Choose between video or audio download (Spotify links automatically use audio-only mode)
4. Select your desired quality and format
5. Click "Download" and wait for the process to complete
6. Your file will automatically download when ready

### Playlist Downloads

1. For YouTube or Spotify playlists, paste the playlist URL and click "Check Formats"
2. Select which tracks/videos you want to download from the playlist
3. Choose your desired quality and format
4. Click "Download" and wait for the process to complete
5. Your playlist will be downloaded and packaged as a ZIP file

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) for the powerful download engine
- [FastAPI](https://fastapi.tiangolo.com/) for the efficient backend
- [React](https://reactjs.org/) for the frontend framework

\* *Note: Due to Spotify's DRM protection, direct downloads are not possible. For Spotify URLs, the app will search for similar tracks on YouTube as an alternative source. This is provided for educational purposes only.*