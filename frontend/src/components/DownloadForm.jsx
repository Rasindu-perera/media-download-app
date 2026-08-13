import React, { useState } from 'react';
import axios from 'axios';

const API_URL = 'http://localhost:8000/api';

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
      setIsPlaylist(playlistResponse.data.is_playlist);
      
      if (playlistResponse.data.is_playlist) {
        setPlaylistInfo(playlistResponse.data);
        // By default select all playlist items
        setSelectedIndices([...Array(playlistResponse.data.items.length).keys()]);
        setFormats(null);
      } else {
        // Get available formats
        const formatsResponse = await axios.post(`${API_URL}/formats`, { url });
        setFormats(formatsResponse.data);
      }
      
      // Extract and save video title
      const videoTitle = playlistResponse.data.is_playlist 
        ? playlistResponse.data.playlist_title 
        : (playlistResponse.data.items[0]?.title || 'Unknown video');
      
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
  
  const startDownload = async (e) => {
    e.preventDefault();
    if (!url) return;
    
    setLoading(true);
    setError(null);
    
    try {
      const quality = mediaType === 'video' ? videoQuality : audioQuality;
      const fileFormat = mediaType === 'video' ? videoFormat : audioFormat;
      
      const response = await axios.post(`${API_URL}/download`, {
        url,
        format_type: mediaType,
        quality,
        file_format: fileFormat,
        is_playlist: isPlaylist,
        selected_indices: isPlaylist ? selectedIndices : null
      });
      
      if (response.data.status === 'success') {
        // Fallback in case backend returns immediate success instead of a task
        setDownloadStatus({
          taskId: 'direct',
          status: 'completed',
          progress: 100,
          statusText: response.data.message || 'Saved to Downloads'
        });
        setLoading(false);
        return;
      }
      
      const taskId = response.data.task_id;
      
      // Start polling for progress
      setDownloadStatus({
        taskId,
        status: 'queued',
        progress: 0
      });
      
      const pollInterval = setInterval(async () => {
        try {
          const progressResponse = await axios.get(`${API_URL}/progress/${taskId}`);
          const { status, progress, error, file_path, status_text } = progressResponse.data;
          
          setDownloadStatus({ taskId, status, progress, error, filePath: file_path, statusText: status_text });
          
          if (status === 'completed') {
            clearInterval(pollInterval);
            try {
              // Trigger backend to move file to Downloads
              const downloadResponse = await axios.get(`${API_URL}/download/${taskId}`);
              setDownloadStatus({ 
                taskId, 
                status, 
                progress, 
                error, 
                filePath: file_path, 
                statusText: downloadResponse.data.message || 'Download Complete! Saved to your Downloads folder.' 
              });
            } catch (err) {
              setError(`Failed to save file: ${err.response?.data?.detail || err.message}`);
            }
          } else if (status === 'error') {
            clearInterval(pollInterval);
            setError(`Download failed: ${error}`);
          }
        } catch (err) {
          clearInterval(pollInterval);
          setError(`Failed to get progress: ${err.message}`);
        }
      }, 1000);
      
    } catch (err) {
      setError(`Failed to start download: ${err.response?.data?.detail || err.message}`);
    } finally {
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
    <>
      <div className="desktop-header">
        <form onSubmit={fetchFormats}>
          <div className="mb-3">
            <label htmlFor="url" className="form-label">Video/Audio URL</label>
            <div className="input-group">
              <input
                type="text"
                className="form-control"
                id="url"
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
                {loading && !formats && !playlistInfo ? 'Loading...' : 'Check Formats'}
              </button>
            </div>
            <div className="form-text mt-1">
              Supports single videos, YouTube playlists, and Spotify tracks/playlists
            </div>
          </div>
        </form>

        {(formats || playlistInfo) && (
          <form onSubmit={startDownload} className="mt-3 pt-3 border-top border-secondary" style={{ borderColor: 'var(--border-light) !important' }}>
            <div className="row g-3 align-items-end">
              <div className="col-md-3">
                <label className="form-label">Media Type</label>
                <div className="btn-group w-100" role="group">
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
                  <label className="btn btn-outline-primary" htmlFor="audioType">Audio</label>
                </div>
              </div>
              
              {mediaType === 'video' ? (
                <>
                  <div className="col-md-3">
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
                      <option value="4K">4K (2160p)</option>
                    </select>
                  </div>
                  <div className="col-md-3">
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
                </>
              ) : (
                <>
                  <div className="col-md-3">
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
                  <div className="col-md-3">
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
                </>
              )}
              
              <div className="col-md-3">
                <button 
                  type="submit" 
                  className="btn btn-primary w-100"
                  disabled={loading || (isPlaylist && selectedIndices.length === 0)}
                >
                  {loading && (formats || playlistInfo) ? 'Processing...' : 
                   isPlaylist ? 
                     `Download (${selectedIndices.length})` : 
                     'Download'}
                </button>
              </div>
            </div>
          </form>
        )}
      </div>

      <div className="desktop-content">
        {error && (
          <div className="alert alert-danger">{error}</div>
        )}
        
        {url.includes('spotify.com') && !formats && !playlistInfo && (
          <div className="alert alert-warning" role="alert">
            <i className="bi bi-exclamation-triangle"></i> Spotify DRM protection detected. Will use fallback search.
          </div>
        )}

        {videoTitle && !isPlaylist && (
          <div className="card mb-3">
            <div className="card-header">
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
              <div className="mb-0 text-muted">
                <small>Download will be saved as: {videoTitle.replace(/[^a-zA-Z0-9]/g, '_').substring(0, 30)}{mediaType === 'video' ? `.${videoFormat}` : `.${audioFormat}`}</small>
              </div>
            </div>
          </div>
        )}
        
        {playlistInfo && playlistInfo.is_playlist && (
          <div className="card h-100 d-flex flex-column" style={{ border: 'none', backgroundColor: 'transparent' }}>
            <div className="d-flex justify-content-between align-items-center mb-3">
              <h5 className="mb-0">
                {playlistInfo.platform === 'spotify' ? '🎵' : '🎬'} {playlistInfo.playlist_title}
              </h5>
              <div className="d-flex align-items-center">
                <span className="badge bg-secondary me-3">
                  {playlistInfo.count} {playlistInfo.platform === 'spotify' ? 'tracks' : 'videos'}
                </span>
                <button 
                  type="button" 
                  className="btn btn-sm btn-outline-primary me-2" 
                  onClick={toggleSelectAll}
                >
                  {selectAll ? 'Deselect All' : 'Select All'}
                </button>
                <span className="text-muted small">{selectedIndices.length} selected</span>
              </div>
            </div>
            
            <div className="playlist-items flex-grow-1" style={{ overflowY: 'auto' }}>
              {playlistInfo.items.map((item, index) => (
                <div 
                  key={item.id} 
                  className={`playlist-item d-flex align-items-center p-3 ${selectedIndices.includes(index) ? 'bg-light' : ''}`}
                  style={{cursor: 'pointer'}}
                  onClick={() => togglePlaylistItem(index)}
                >
                  <div className="form-check me-3">
                    <input
                      type="checkbox"
                      className="form-check-input"
                      checked={selectedIndices.includes(index)}
                      onChange={() => {}}
                      onClick={(e) => e.stopPropagation()}
                    />
                  </div>
                  <div className="flex-grow-1">
                    <div className="fw-bold">{index + 1}. {item.title}</div>
                    {item.artist && (
                      <div className="text-muted small mt-1">Artist: {item.artist}</div>
                    )}
                  </div>
                  <div className="text-muted small ms-3">
                    {formatDuration(item.duration)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </>
  );
};

export default DownloadForm;