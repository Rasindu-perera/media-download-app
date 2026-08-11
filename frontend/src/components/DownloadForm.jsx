import React, { useState } from 'react';
import axios from 'axios';

const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000/api';

/**
 * Trigger a browser file download by routing the URL through our /api/proxy
 * endpoint. This makes the download same-origin, which means:
 *   1. The link.download attribute is respected (file is saved to disk).
 *   2. The browser shows the "allow multiple downloads" prompt for playlists.
 */
function triggerDownload(rawUrl, filename) {
  const proxyUrl =
    `${API_URL}/proxy?url=${encodeURIComponent(rawUrl)}&filename=${encodeURIComponent(filename)}`;
  const link = document.createElement('a');
  link.href = proxyUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

/** Build a safe filename from a title + extension. */
function buildFilename(title, ext) {
  const safe = (title || 'download')
    .replace(/[^\w\s-]/g, '')
    .trim()
    .replace(/\s+/g, '_')
    .substring(0, 60);
  return `${safe}.${ext}`;
}

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
  const [videoAuthor, setVideoAuthor] = useState('');

  const handleUrlChange = (e) => {
    const newUrl = e.target.value;
    setUrl(newUrl);
    setFormats(null);
    setPlaylistInfo(null);
    setIsPlaylist(false);
    setVideoTitle('');
    setVideoAuthor('');
    // Auto-switch to audio for Spotify URLs
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
      // Check playlist / single info
      const playlistResponse = await axios.post(`${API_URL}/playlist-info`, { url });
      const pData = playlistResponse.data;

      setIsPlaylist(pData.type === 'playlist');

      if (pData.type === 'playlist') {
        setPlaylistInfo(pData);
        setSelectedIndices([...Array(pData.items.length).keys()]);
        setVideoTitle(pData.playlist_title || '');
        setVideoAuthor('');
      } else {
        // Single track / video
        const title = pData.data?.title || pData.playlist_title || '';
        const author = pData.data?.artist || pData.data?.author || '';
        setVideoTitle(title);
        setVideoAuthor(author);
      }

      // Get available formats (static response)
      const formatsResponse = await axios.post(`${API_URL}/formats`, { url });
      setFormats(formatsResponse.data);

    } catch (err) {
      setError(`Failed to get info: ${err.response?.data?.detail || err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const toggleSelectAll = () => {
    if (!playlistInfo) return;
    if (selectAll) {
      setSelectedIndices([]);
      setSelectAll(false);
    } else {
      setSelectedIndices([...Array(playlistInfo.items.length).keys()]);
      setSelectAll(true);
    }
  };

  const togglePlaylistItem = (index) => {
    setSelectedIndices((prev) =>
      prev.includes(index) ? prev.filter((i) => i !== index) : [...prev, index]
    );
  };

  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  const startDownload = async (e) => {
    e.preventDefault();
    if (!url) return;

    setLoading(true);
    setError(null);

    const quality = mediaType === 'video' ? videoQuality : audioQuality;
    const fileFormat = mediaType === 'video' ? videoFormat : audioFormat;

    // ------------------------------------------------------------------
    // Single download
    // ------------------------------------------------------------------
    if (!isPlaylist) {
      try {
        setDownloadStatus({
          taskId: 'single',
          status: 'downloading',
          progress: 50,
          error: 'Fetching stream URL…',
        });

        const response = await axios.post(`${API_URL}/download`, {
          url,
          format_type: mediaType,
          quality,
          file_format: fileFormat,
          is_playlist: false,
          selected_indices: null,
        });

        if (response.data.url) {
          setDownloadStatus({
            taskId: 'single',
            status: 'completed',
            progress: 100,
            error: null,
          });

          // Use actual_format from backend if available (e.g. m4a instead of mp3)
          const actualExt = response.data.actual_format || fileFormat;
          const filename = buildFilename(videoTitle, actualExt);
          triggerDownload(response.data.url, filename);
        } else {
          setError('No download URL returned.');
        }
      } catch (err) {
        setError(
          `Failed to start download: ${err.response?.data?.detail || err.message}`
        );
        setLoading(false);
      }
      return;
    }

    // ------------------------------------------------------------------
    // Playlist sequential download
    // ------------------------------------------------------------------
    if (selectedIndices.length === 0) {
      setError('Please select at least one track to download.');
      setLoading(false);
      return;
    }

    const itemsToDownload = selectedIndices.map((i) => playlistInfo.items[i]);
    let successCount = 0;
    let failedCount = 0;

    for (let i = 0; i < itemsToDownload.length; i++) {
      const item = itemsToDownload[i];

      setDownloadStatus({
        taskId: 'queue',
        status: 'downloading',
        progress: Math.round((i / itemsToDownload.length) * 100),
        error: `Downloading ${i + 1}/${itemsToDownload.length}: ${item.title}`,
      });

      try {
        const response = await axios.post(`${API_URL}/download`, {
          url: item.id,
          format_type: mediaType,
          quality,
          file_format: fileFormat,
          is_playlist: false,
          selected_indices: null,
        });

        if (response.data.url) {
          const actualExt = response.data.actual_format || fileFormat;
          const trackNum = (i + 1).toString().padStart(3, '0');
          const filename = buildFilename(`${trackNum}_${item.title}`, actualExt);
          triggerDownload(response.data.url, filename);
          successCount++;
        } else {
          throw new Error('No URL returned from backend');
        }

        // Anti-rate-limit delay between tracks (not on the last one)
        if (i < itemsToDownload.length - 1) {
          setDownloadStatus({
            taskId: 'queue',
            status: 'downloading',
            progress: Math.round(((i + 1) / itemsToDownload.length) * 100),
            error: `Waiting 4 s before next track…`,
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
      error: `Done! ✅ ${successCount} downloaded, ❌ ${failedCount} failed.`,
    });
    setLoading(false);
  };

  const formatDuration = (seconds) => {
    if (!seconds) return 'Unknown';
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s.toString().padStart(2, '0')}`;
  };

  return (
    <div className="download-form">
      {/* Spotify notice */}
      {url.includes('spotify.com') && (
        <div className="alert alert-warning" role="alert">
          🎵 Spotify URLs are resolved to YouTube audio tracks for download.
        </div>
      )}

      {/* URL input form */}
      <form onSubmit={fetchFormats}>
        <div className="mb-3">
          <label htmlFor="url" className="form-label">Video / Audio URL</label>
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
              {loading ? 'Loading…' : 'Check Formats'}
            </button>
          </div>
          <div className="form-text">
            Supports single videos, YouTube playlists, and Spotify tracks/playlists
          </div>
        </div>
      </form>

      {/* Single video / track info card */}
      {videoTitle && !isPlaylist && (
        <div className="card mb-3">
          <div className="card-header bg-primary text-white">
            <h5 className="mb-0">
              {url.includes('spotify.com') ? '🎵 Track Information' : '🎬 Video Information'}
            </h5>
          </div>
          <div className="card-body">
            <div className="mb-1">
              <strong>Title:</strong> {videoTitle}
            </div>
            {videoAuthor && (
              <div className="mb-1">
                <strong>{url.includes('spotify.com') ? 'Artist' : 'Channel'}:</strong> {videoAuthor}
              </div>
            )}
            <div className="mb-0 text-muted small">
              File will be saved as:{' '}
              <code>
                {buildFilename(
                  videoTitle,
                  mediaType === 'video' ? videoFormat : audioFormat
                )}
              </code>
            </div>
          </div>
        </div>
      )}

      {/* Error */}
      {error && <div className="alert alert-danger">{error}</div>}

      {/* Playlist items */}
      {playlistInfo && playlistInfo.type === 'playlist' && (
        <div className="card mb-3">
          <div className="card-header bg-info text-white d-flex justify-content-between align-items-center">
            <h5 className="mb-0">
              {playlistInfo.platform === 'spotify' ? '🎵' : '🎬'}{' '}
              {playlistInfo.playlist_title}
            </h5>
            <span className="badge bg-light text-dark">
              {playlistInfo.count}{' '}
              {playlistInfo.platform === 'spotify' ? 'tracks' : 'videos'}
            </span>
          </div>
          <div className="card-body">
            <div className="alert alert-info alert-sm" role="alert" style={{ fontSize: '0.9rem' }}>
              <i className="bi bi-info-circle-fill me-2"></i>
              Your browser will ask to <strong>allow multiple downloads</strong> after the
              second track starts. Click <em>Allow</em> to let the playlist download
              automatically.
            </div>
            <div className="d-flex justify-content-between mb-3">
              <button
                type="button"
                className="btn btn-sm btn-outline-primary"
                onClick={toggleSelectAll}
              >
                {selectAll ? 'Deselect All' : 'Select All'}
              </button>
              <span className="text-muted">
                {selectedIndices.length} of {playlistInfo.count} selected
              </span>
            </div>

            <div className="playlist-items" style={{ maxHeight: '300px', overflowY: 'auto' }}>
              {playlistInfo.items.map((item, index) => (
                <div
                  key={index}
                  className={`playlist-item d-flex align-items-center p-2 ${
                    selectedIndices.includes(index) ? 'bg-light border' : ''
                  }`}
                  style={{ cursor: 'pointer', borderRadius: '4px', marginBottom: '5px' }}
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
                  </div>
                  <div className="ms-2 flex-grow-1">
                    <div className="fw-bold">{index + 1}. {item.title}</div>
                    {item.artist && (
                      <div className="text-muted small">Artist: {item.artist}</div>
                    )}
                    <div className="text-muted small">
                      Duration: {formatDuration(item.duration)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Format / quality selection + download button */}
      {formats && (
        <form onSubmit={startDownload}>
          <div className="mb-3">
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
              <label
                className={`btn btn-outline-primary ${url.includes('spotify.com') ? 'disabled' : ''}`}
                htmlFor="videoType"
              >
                Video
              </label>

              <input
                type="radio"
                className="btn-check"
                name="mediaType"
                id="audioType"
                value="audio"
                checked={mediaType === 'audio' || url.includes('spotify.com')}
                onChange={() => setMediaType('audio')}
              />
              <label className="btn btn-outline-primary" htmlFor="audioType">
                Audio Only
              </label>
            </div>
            {url.includes('spotify.com') && (
              <div className="form-text text-warning">
                ⚠️ Spotify links are audio-only (resolved to YouTube audio).
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
                  <option value="720p">720p (max for direct stream)</option>
                  <option value="1080p">1080p</option>
                  <option value="1440p">1440p</option>
                  <option value="2160p">4K (2160p)</option>
                </select>
                <div className="form-text text-muted">
                  Note: YouTube progressive streams max at 720p. Higher qualities use adaptive streams.
                </div>
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
                <div className="form-text text-muted">
                  Note: YouTube audio is served as M4A. The file plays in all modern players.
                </div>
              </div>
            </div>
          )}

          <div className="d-grid gap-2">
            <button
              type="submit"
              className="btn btn-primary"
              disabled={loading || (isPlaylist && selectedIndices.length === 0)}
            >
              {loading
                ? 'Processing…'
                : isPlaylist
                ? `Download ${selectedIndices.length} ${
                    playlistInfo?.platform === 'spotify' ? 'tracks' : 'videos'
                  }`
                : 'Download'}
            </button>
          </div>
        </form>
      )}
    </div>
  );
};

export default DownloadForm;