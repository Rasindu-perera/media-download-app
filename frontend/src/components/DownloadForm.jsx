import React, { useState } from 'react';
import axios from 'axios';

const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000/api';

const DownloadForm = ({ setDownloadStatus }) => {
  const [url, setUrl] = useState('');
  const [mediaType, setMediaType] = useState('video');
  const [videoQuality, setVideoQuality] = useState('720p');
  const [videoFormat, setVideoFormat] = useState('mp4');
  const [audioQuality, setAudioQuality] = useState('128k');
  const [audioFormat, setAudioFormat] = useState('mp3');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [formats, setFormats] = useState(null);
  const [playlistInfo, setPlaylistInfo] = useState(null);
  const [isPlaylist, setIsPlaylist] = useState(false);
  const [selectedIndices, setSelectedIndices] = useState([]);
  const [selectAll, setSelectAll] = useState(true);
  const [videoTitle, setVideoTitle] = useState('');
    const handleUrlChange = (e) => {
    const newUrl = e.target.value;
    setUrl(newUrl);
    setFormats(null);
    setPlaylistInfo(null);
    setIsPlaylist(false);
    
    // Auto-switch to audio type for Spotify URLs
    if (newUrl.includes('spotify.com') && mediaType === 'video') {
      setMediaType('audio');
    }
  };
  
  const fetchFormats = async (e) => {
    e.preventDefault();
    if (!url) return;
    
    setLoading(true);
    setError(null);
    
    try {
      // First check if it's a playlist
      const playlistResponse = await axios.post(`${API_URL}/playlist-info`, { url });
      setIsPlaylist(playlistResponse.data.type === 'playlist');
      
      if (playlistResponse.data.type === 'playlist') {
        setPlaylistInfo(playlistResponse.data);
        // By default select all playlist items
        setSelectedIndices([...Array(playlistResponse.data.items.length).keys()]);
      }
      
      // Get available formats
      const formatsResponse = await axios.post(`${API_URL}/formats`, { url });
      setFormats(formatsResponse.data);
      
      // Extract and save video title
      const videoTitle = playlistResponse.data.type === 'playlist' 
        ? playlistResponse.data.playlist_title 
        : (playlistResponse.data.data?.title || 'Unknown video');
      
      setVideoTitle(videoTitle);  // Add this state variable
    } catch (err) {
      setError(`Failed to get formats: ${err.response?.data?.detail || err.message}`);
    } finally {
      setLoading(false);
    }
  };
  
  const toggleSelectAll = () => {
    if (!playlistInfo) return;
    
    if (selectAll) {
      // Deselect all
      setSelectedIndices([]);
      setSelectAll(false);
    } else {
      // Select all
      setSelectedIndices([...Array(playlistInfo.items.length).keys()]);
      setSelectAll(true);
    }
  };
  
  const togglePlaylistItem = (index) => {
    setSelectedIndices(prev => {
      if (prev.includes(index)) {
        return prev.filter(i => i !== index);
      } else {
        return [...prev, index];
      }
    });
  };
  
  
  const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));

  const startDownload = async (e) => {
    e.preventDefault();
    if (!url) return;
    
    setLoading(true);
    setError(null);
    
    const quality = mediaType === 'video' ? videoQuality : audioQuality;
    const fileFormat = mediaType === 'video' ? videoFormat : audioFormat;
    
    if (!isPlaylist) {
      // Single video download logic
      try {
        setDownloadStatus({ taskId: 'cobalt', status: 'downloading', progress: 50, error: 'Requesting direct link from Cobalt API...' });
        const response = await axios.post(`${API_URL}/download`, {
          url,
          format_type: mediaType,
          quality,
          file_format: fileFormat,
          is_playlist: false,
          selected_indices: null
        });
        
        if (response.data.url) {
            setDownloadStatus({ taskId: 'cobalt', status: 'completed', progress: 100, error: null });
            const link = document.createElement('a');
            link.href = response.data.url;
            link.target = '_blank';
            link.download = '';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        } else {
            setError('Failed to get download URL');
        }
      } catch (err) {
        setError(`Failed to start download: ${err.response?.data?.detail || err.message}`);
        setLoading(false);
      }
    } else {
      // Playlist sequential queueing logic
      if (selectedIndices.length === 0) {
        setError("Please select at least one track to download.");
        setLoading(false);
        return;
      }
      
      const itemsToDownload = selectedIndices.map(index => playlistInfo.items[index]);
      let successCount = 0;
      let failedCount = 0;
      
      for (let i = 0; i < itemsToDownload.length; i++) {
        const item = itemsToDownload[i];
        const safeTitle = item.title.replace(/[^a-zA-Z0-9]/g, '_').substring(0, 30);
        const filename = `${(i+1).toString().padStart(3, '0')}_${safeTitle}.${fileFormat}`;
        
        setDownloadStatus({
          taskId: 'queue',
          status: 'downloading',
          progress: 0,
          error: `Downloading track ${i + 1} of ${itemsToDownload.length}: ${item.title}`
        });
        
        try {
          const itemUrl = item.id; // item.id is the direct YouTube URL returned by backend
          
          setDownloadStatus({
            taskId: 'queue',
            status: 'downloading',
            progress: 50,
            error: `Requesting Cobalt link for track ${i + 1} of ${itemsToDownload.length}: ${item.title}`
          });

          const response = await axios.post(`${API_URL}/download`, {
            url: itemUrl,
            format_type: mediaType,
            quality,
            file_format: fileFormat,
            is_playlist: false,
            selected_indices: null
          });
          
          if (response.data.url) {
              const link = document.createElement('a');
              link.href = response.data.url;
              link.target = '_blank';
              link.download = '';
              document.body.appendChild(link);
              link.click();
              document.body.removeChild(link);
              successCount++;
          } else {
              throw new Error('No URL returned from backend');
          }
          
          // Add anti-ban delay strictly to 4 seconds
          if (i < itemsToDownload.length - 1) {
            setDownloadStatus({
              taskId: 'queue',
              status: 'downloading',
              progress: 100,
              error: `Waiting 4 seconds before next track to prevent rate-limiting...`
            });
            await delay(4000);
          }
          
        } catch (err) {
          console.error(`Failed to download ${item.title}:`, err);
          failedCount++;
        }
      }
      
      setDownloadStatus({
        taskId: 'queue_done',
        status: 'completed',
        progress: 100,
        error: `Playlist finished! ${successCount} downloaded, ${failedCount} failed.`
      });
      setLoading(false);
    }
  };
  
  const formatDuration = (seconds) => {
    if (!seconds) return 'Unknown';
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;
    return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`;
  };
    return (
    <div className="download-form">
      {url.includes('spotify.com') && (
        <div className="alert alert-warning" role="alert">
          
        </div>
      )}
      
      <form onSubmit={fetchFormats}>
        <div className="mb-3">
          <label htmlFor="url" className="form-label">Video/Audio URL</label>
          <div className="input-group">
            <input
              type="text"
              className="form-control"              id="url"
              value={url}
              onChange={handleUrlChange}
              placeholder="Paste YouTube, Facebook, Instagram, TikTok, or Spotify URL"
              required
            />
            <button 
              type="submit" 
              className="btn btn-outline-primary"
              disabled={loading || !url}
            >
              {loading ? 'Loading...' : 'Check Formats'}
            </button>
          </div>          <div className="form-text">
            Supports single videos, YouTube playlists, and Spotify tracks/playlists
          </div>
        </div>
      </form>
        {videoTitle && !isPlaylist && (
        <div className="card mb-3">
          <div className="card-header bg-primary text-white">
            <h5 className="mb-0">
              {url.includes('spotify.com') ? 'Track Information' : 'Video Information'}
            </h5>
          </div>
          <div className="card-body">
            <div className="mb-2">
              <strong>Title:</strong> {videoTitle}
            </div>
            {url.includes('spotify.com') && formats && formats.items && formats.items[0]?.artist && (
              <div className="mb-2">
                <strong>Artist:</strong> {formats.items[0].artist}
              </div>
            )}
            <div className="mb-0">
              <strong>Download will be saved as:</strong> {videoTitle.replace(/[^a-zA-Z0-9]/g, '_').substring(0, 30)}
              {mediaType === 'video' ? `.${videoFormat}` : `.${audioFormat}`}
            </div>
          </div>
        </div>
      )}
      
      {error && (
        <div className="alert alert-danger">{error}</div>
      )}
      
      {playlistInfo && playlistInfo.type === 'playlist' && (
        <div className="card mb-3">
          <div className="card-header bg-info text-white d-flex justify-content-between align-items-center">
            <h5 className="mb-0">
              {playlistInfo.platform === 'spotify' ? '🎵' : '🎬'} {playlistInfo.playlist_title}
            </h5>
            <span className="badge bg-light text-dark">
              {playlistInfo.count} {playlistInfo.platform === 'spotify' ? 'tracks' : 'videos'}
            </span>
          </div>
          <div className="card-body">
            <div className="alert alert-info alert-sm" role="alert" style={{ fontSize: '0.9rem' }}>
              <i className="bi bi-info-circle-fill me-2"></i>
              Note: Your browser may ask for permission to download multiple files. Please click 'Allow' to let the playlist download automatically.
            </div>
            <div className="d-flex justify-content-between mb-3">
              <button 
                type="button" 
                className="btn btn-sm btn-outline-primary" 
                onClick={toggleSelectAll}
              >
                {selectAll ? 'Deselect All' : 'Select All'}
              </button>
              <span className="text-muted">{selectedIndices.length} of {playlistInfo.count} selected</span>
            </div>
            
            <div className="playlist-items" style={{maxHeight: '300px', overflowY: 'auto'}}>
              {playlistInfo.items.map((item, index) => (
                <div 
                  key={item.id} 
                  className={`playlist-item d-flex align-items-center p-2 ${selectedIndices.includes(index) ? 'bg-light border' : ''}`}
                  style={{cursor: 'pointer', borderRadius: '4px', marginBottom: '5px'}}
                  onClick={() => togglePlaylistItem(index)}
                >
                  <div className="form-check me-2">
                    <input
                      type="checkbox"
                      className="form-check-input"
                      checked={selectedIndices.includes(index)}
                      onChange={() => {}}
                      onClick={(e) => e.stopPropagation()}
                    />
                  </div>                  <div className="ms-2 flex-grow-1">
                    <div className="fw-bold">{index + 1}. {item.title}</div>
                    {item.artist && (
                      <div className="text-muted small">Artist: {item.artist}</div>
                    )}
                    <div className="text-muted small">Duration: {formatDuration(item.duration)}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
      
      {formats && (
        <form onSubmit={startDownload}>          <div className="mb-3">
            <label className="form-label">Media Type</label>
            <div className="btn-group d-flex" role="group">
              <input
                type="radio"
                className="btn-check"
                name="mediaType"
                id="videoType"
                value="video"
                checked={mediaType === 'video'}
                onChange={() => setMediaType('video')}
                disabled={url.includes('spotify.com')}
              />
              <label className={`btn btn-outline-primary ${url.includes('spotify.com') ? 'disabled' : ''}`} htmlFor="videoType">Video</label>
              
              <input
                type="radio"
                className="btn-check"
                name="mediaType"
                id="audioType"
                value="audio"
                checked={mediaType === 'audio' || url.includes('spotify.com')}
                onChange={() => setMediaType('audio')}
              />
              <label className="btn btn-outline-primary" htmlFor="audioType">Audio Only</label>
            </div>            {url.includes('spotify.com') && (
              <div className="form-text text-warning">
                <i className="bi bi-exclamation-triangle"></i> 
              </div>
            )}
          </div>
          
          {mediaType === 'video' ? (
            <div className="row mb-3">
              <div className="col-md-6">
                <label htmlFor="videoQuality" className="form-label">Video Quality</label>
                <select
                  className="form-select"
                  id="videoQuality"
                  value={videoQuality}
                  onChange={(e) => setVideoQuality(e.target.value)}
                >
                  <option value="480p">480p</option>
                  <option value="720p">720p</option>
                  <option value="1080p">1080p</option>
                  <option value="1440p">1440p</option>
                  <option value="2160p">4K (2160p)</option>
                </select>
              </div>
              <div className="col-md-6">
                <label htmlFor="videoFormat" className="form-label">Format</label>
                <select
                  className="form-select"
                  id="videoFormat"
                  value={videoFormat}
                  onChange={(e) => setVideoFormat(e.target.value)}
                >
                  <option value="mp4">MP4</option>
                  <option value="webm">WebM</option>
                </select>
              </div>
            </div>
          ) : (
            <div className="row mb-3">
              <div className="col-md-6">
                <label htmlFor="audioQuality" className="form-label">Audio Quality</label>
                <select
                  className="form-select"
                  id="audioQuality"
                  value={audioQuality}
                  onChange={(e) => setAudioQuality(e.target.value)}
                >
                  <option value="128k">128 kbps</option>
                  <option value="256k">256 kbps</option>
                  <option value="320k">320 kbps</option>
                </select>
              </div>
              <div className="col-md-6">
                <label htmlFor="audioFormat" className="form-label">Format</label>
                <select
                  className="form-select"
                  id="audioFormat"
                  value={audioFormat}
                  onChange={(e) => setAudioFormat(e.target.value)}
                >
                  <option value="mp3">MP3</option>
                  <option value="m4a">M4A</option>
                  <option value="opus">Opus</option>
                </select>
              </div>
            </div>
          )}
          
          <div className="d-grid gap-2">            
            <button 
              type="submit" 
              className="btn btn-primary"
              disabled={loading || (isPlaylist && selectedIndices.length === 0)}
            >
              {loading ? 'Processing...' : 
               isPlaylist ? 
                 `Download ${selectedIndices.length} ${playlistInfo?.platform === 'spotify' ? 'tracks' : 'videos'}` : 
                 'Download'}
            </button>
          </div>
        </form>
      )}
    </div>
  );
};

export default DownloadForm;