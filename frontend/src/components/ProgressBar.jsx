import React from 'react';

const ProgressBar = ({ downloadStatus }) => {
  const { status, progress, error, statusText } = downloadStatus;
  
  const getProgressBarClass = () => {
    switch (status) {
      case 'queued':
        return 'progress-bar progress-bar-striped progress-bar-animated bg-info';
      case 'downloading':
      case 'processing':
        return 'progress-bar progress-bar-striped progress-bar-animated';
      case 'completed':
        return 'progress-bar bg-success';
      case 'error':
        return 'progress-bar bg-danger';
      default:
        return 'progress-bar';
    }
  };
  
  const getStatusText = () => {
    switch (status) {
      case 'queued':
        return 'Preparing download...';
      case 'downloading':
      case 'processing':
        // Display our new detailed status text if provided by backend
        if (statusText) {
          return progress > 0 && progress < 100 
            ? `${statusText} (${Math.round(progress)}%)` 
            : statusText;
        }
        // Legacy fallback
        if (error && error.startsWith('Downloading ')) {
          return error;
        }
        if (status === 'processing') return 'Processing file...';
        return `Downloading: ${Math.round(progress)}%`;
      case 'completed':
        return 'Download completed!';
      case 'error':
        return `Error: ${error || 'Unknown error'}`;
      default:
        return 'Unknown status';
    }
  };
  
  return (
    <div className="download-progress rounded p-3" style={{ backgroundColor: 'rgba(255, 255, 255, 0.03)', border: '1px solid var(--border-light)' }}>
      <div className="d-flex justify-content-between align-items-end mb-2">
        <h6 className="mb-0 fw-bold" style={{ color: 'var(--text-main)' }}>{getStatusText()}</h6>
        <span className="text-muted small fw-bold">{Math.round(progress)}%</span>
      </div>
      <div className="progress">
        <div 
          className={getProgressBarClass()}
          role="progressbar"
          style={{ width: `${progress}%` }}
          aria-valuenow={progress}
          aria-valuemin="0"
          aria-valuemax="100"
        />
      </div>
      
      {status === 'completed' && (
        <div className="mt-3 text-center">
          <p className="mb-0" style={{ color: 'var(--accent)', fontWeight: '600' }}>
            <i className="bi bi-check-circle-fill me-2"></i>
            {downloadStatus.statusText || 'Your download has completed!'}
          </p>
        </div>
      )}
    </div>
  );
};

export default ProgressBar;