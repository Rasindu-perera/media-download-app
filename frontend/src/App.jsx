import React, { useState } from 'react';
import 'bootstrap/dist/css/bootstrap.min.css';
import './App.css';
import DownloadForm from './components/DownloadForm';
import ProgressBar from './components/ProgressBar';

function App() {
  const [downloadStatus, setDownloadStatus] = useState(null);
  
  return (
    <div className="App">
      <div className="container mt-5">
        <div className="row justify-content-center">
          <div className="col-md-8">
            <div className="card shadow">
              <div className="card-header bg-primary text-white">
                <h2 className="text-center mb-0">Media Downloader</h2>
              </div>
              <div className="card-body">
                <DownloadForm setDownloadStatus={setDownloadStatus} />
                
                {downloadStatus && (
                  <div className="mt-4">
                    <ProgressBar downloadStatus={downloadStatus} />
                  </div>
                )}
              </div>              <div className="card-footer text-center text-muted">
                Supports YouTube, Facebook, Instagram, TikTok, and Spotify | Developed by KWR
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;