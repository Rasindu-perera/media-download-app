import React from 'react';

const ProgressBar = ({ downloadStatus }) => {
  const { status, progress, error } = downloadStatus;
  
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
        // Check if there's item-specific or queue information in the error field
        if (error && (error.startsWith('Downloading ') || error.startsWith('Waiting '))) {
          return error;
        }
        return `Downloading: ${Math.round(progress)}%`;
      case 'processing':
        return 'Processing file...';
      case 'completed':
        return 'Download completed!';
      case 'error':
        return `Error: ${error || 'Unknown error'}`;
      default:
        return 'Unknown status';
    }
  };
  
  return (
    <div className="download-progress">
      <h5 className="mb-2">{getStatusText()}</h5>
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
      
      {status === 'completed' && downloadStatus.taskId !== 'queue_done' && (
        <div className="mt-3 text-center">
          <p className="text-success mb-2">Your download has completed!</p>
        </div>
      )}
      
      {status === 'completed' && downloadStatus.taskId === 'queue_done' && (
        <div className="mt-3 text-center">
          <p className="text-success mb-2">{error || "Your playlist has finished downloading!"}</p>
        </div>
      )}
    </div>
  );
};

export default ProgressBar;