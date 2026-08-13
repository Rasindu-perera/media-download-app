import React, { useState } from 'react';
import 'bootstrap/dist/css/bootstrap.min.css';
import './App.css';
import DownloadForm from './components/DownloadForm';
import ProgressBar from './components/ProgressBar';

function App() {
  const [downloadStatus, setDownloadStatus] = useState(null);
  
  return (
    <div className="desktop-app-container">
      <div className="desktop-header-title">
        <h2>Media Downloader</h2>
      </div>
      <div className="desktop-main-wrapper">
        <DownloadForm setDownloadStatus={setDownloadStatus} />
        
        {downloadStatus && (
          <div className="desktop-progress-container">
            <ProgressBar downloadStatus={downloadStatus} />
          </div>
        )}
      </div>
      <div className="desktop-footer text-muted">
        Supports YouTube, Facebook, Instagram, TikTok, and Spotify | Developed by KWR
      </div>
    </div>
  );
}

export default App;