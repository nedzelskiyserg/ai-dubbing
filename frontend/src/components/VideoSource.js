import React, { useState, useContext, useRef, useEffect } from 'react';
import './VideoSource.css';
import { AppContext } from '../AppContext';
import Dropdown from './Dropdown';

const VideoSource = () => {
  const { youtubeUrl, setYoutubeUrl, quality, setQuality, uploadedFile, setUploadedFile } = useContext(AppContext);
  const [isDragging, setIsDragging] = useState(false);
  const [fileInfo, setFileInfo] = useState(null);
  const [thumbnailUrl, setThumbnailUrl] = useState(null);
  const fileInputRef = useRef(null);

  // Получаем информацию о файле
  useEffect(() => {
    // Очищаем старое превью при смене файла
    if (thumbnailUrl) {
      URL.revokeObjectURL(thumbnailUrl);
      setThumbnailUrl(null);
    }

    if (uploadedFile) {
      const formatFileSize = (bytes) => {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
      };

      const info = {
        name: uploadedFile.name,
        size: formatFileSize(uploadedFile.size),
        type: uploadedFile.type,
      };

      // Пытаемся получить длительность видео и создать превью
      if (uploadedFile.type.startsWith('video/')) {
        const url = URL.createObjectURL(uploadedFile);
        const video = document.createElement('video');
        video.preload = 'metadata';
        video.crossOrigin = 'anonymous';
        
        video.onloadedmetadata = () => {
          const duration = video.duration;
          const minutes = Math.floor(duration / 60);
          const seconds = Math.floor(duration % 60);
          setFileInfo({
            ...info,
            duration: `${minutes}:${seconds.toString().padStart(2, '0')}`,
          });

          // Создаем превью из видео
          video.currentTime = Math.min(1, duration / 10); // Берем кадр на 1 секунде или 10% от длительности
          
          video.onseeked = () => {
            try {
              const canvas = document.createElement('canvas');
              canvas.width = video.videoWidth;
              canvas.height = video.videoHeight;
              const ctx = canvas.getContext('2d');
              ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
              
              // Конвертируем в blob URL для превью
              canvas.toBlob((blob) => {
                if (blob) {
                  const thumbnailUrl = URL.createObjectURL(blob);
                  setThumbnailUrl(thumbnailUrl);
                }
              }, 'image/jpeg', 0.8);
            } catch (error) {
              console.error('Ошибка создания превью:', error);
            }
          };
        };
        
        video.onerror = () => {
          window.URL.revokeObjectURL(url);
          setFileInfo(info);
        };
        
        video.src = url;
      } else {
        setFileInfo(info);
      }
    } else {
      setFileInfo(null);
    }
  }, [uploadedFile, thumbnailUrl]);

  const handleDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    
    const files = Array.from(e.dataTransfer.files).filter(file => 
      file.type.startsWith('video/')
    );
    
    if (files.length > 0) {
      setUploadedFile(files[0]);
      setYoutubeUrl(''); // Очищаем YouTube URL
    }
  };

  const handleFileInput = (e) => {
    const files = Array.from(e.target.files);
    if (files.length > 0) {
      setUploadedFile(files[0]);
      setYoutubeUrl(''); // Очищаем YouTube URL
    }
  };

  const handleRemoveFile = (e) => {
    e.stopPropagation();
    // Очищаем URL превью при удалении файла
    if (thumbnailUrl) {
      URL.revokeObjectURL(thumbnailUrl);
      setThumbnailUrl(null);
    }
    setUploadedFile(null);
    setFileInfo(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  // Очищаем URL превью при размонтировании или смене файла
  useEffect(() => {
    return () => {
      if (thumbnailUrl) {
        URL.revokeObjectURL(thumbnailUrl);
      }
    };
  }, [thumbnailUrl]);

  const handleFileCardClick = () => {
    fileInputRef.current?.click();
  };

  return (
    <div className="panel">
      <div className="panel-header">
        <span className="panel-label">[INPUT:SOURCE]</span>
        <span className="panel-title">VIDEO SOURCE</span>
        <span className="material-symbols-outlined panel-icon">video_library</span>
      </div>
      <div className="panel-content">
        <div className="yt-input-section">
          <div className="yt-label">PASTE YOUTUBE URL</div>
          <div className="yt-input-row">
            <input
              type="text"
              className="input-field yt-input"
              placeholder="https://youtube.com/watch?v=..."
              value={youtubeUrl}
              onChange={(e) => {
                setYoutubeUrl(e.target.value);
                setUploadedFile(null); // Очищаем файл при вводе URL
              }}
              onPaste={(e) => {
                // Разрешаем стандартную вставку браузера
                // Не вызываем preventDefault - позволяем браузеру обработать вставку
                setUploadedFile(null);
              }}
              onKeyDown={(e) => {
                // Разрешаем все стандартные комбинации клавиш
                // Не блокируем Cmd+V, Ctrl+V, Cmd+C, Ctrl+C и т.д.
              }}
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
              spellCheck="false"
            />
            <div style={{ width: '140px' }}>
              <Dropdown
                className="quality-dropdown"
                value={quality}
                onChange={(value) => setQuality(value)}
                options={[
                  { value: 'max', label: 'Авто (Max)' },
                  { value: '2160p', label: '4K' },
                  { value: '1440p', label: '2K' },
                  { value: '1080p', label: '1080P' },
                  { value: '720p', label: '720P' }
                ]}
              />
            </div>
          </div>
        </div>

        {!uploadedFile && (
          <>
            <div className="divider-or">
              <div className="divider-line"></div>
              <span className="divider-text">OR</span>
              <div className="divider-line"></div>
            </div>

            <div
              className={`dropzone ${isDragging ? 'dragging' : ''}`}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
            >
              <svg 
                className="drop-icon"
                width="48" 
                height="48" 
                viewBox="0 0 24 24" 
                fill="none" 
                xmlns="http://www.w3.org/2000/svg"
              >
                <path 
                  d="M14 2H6C4.9 2 4 2.9 4 4V20C4 21.1 4.89 22 5.99 22H18C19.1 22 20 21.1 20 20V8L14 2Z" 
                  stroke="#4D4D4D" 
                  strokeWidth="1.2" 
                  strokeLinecap="round" 
                  strokeLinejoin="round"
                  fill="none"
                />
                <path 
                  d="M14 2V8H20" 
                  stroke="#4D4D4D" 
                  strokeWidth="1.2" 
                  strokeLinecap="round" 
                  strokeLinejoin="round"
                  fill="none"
                />
                <path 
                  d="M12 11V17" 
                  stroke="#4D4D4D" 
                  strokeWidth="1.2" 
                  strokeLinecap="round" 
                  strokeLinejoin="round"
                />
                <path 
                  d="M9 14L12 11L15 14" 
                  stroke="#4D4D4D" 
                  strokeWidth="1.2" 
                  strokeLinecap="round" 
                  strokeLinejoin="round"
                />
              </svg>
              <div className="drop-text-main">DRAG & DROP VIDEO FILE</div>
              <div className="drop-text-sub">or click to browse</div>
              <div className="drop-formats">
                <span className="format-label">[FORMATS]</span>
                <span className="format-list">MP4 • MKV • AVI • MOV • WEBM</span>
              </div>
            </div>
          </>
        )}

        {uploadedFile && fileInfo && (
          <div className="file-info-section">
            <div className="file-header">
              <span className="file-label">LOCAL FILE</span>
              <button className="remove-btn" onClick={handleRemoveFile}>
                <span className="material-symbols-outlined remove-icon">close</span>
                <span className="remove-text">REMOVE</span>
              </button>
            </div>
            <div 
              className={`file-card ${isDragging ? 'dragging' : ''}`}
              onClick={handleFileCardClick}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
            >
              <div className="file-thumbnail">
                {thumbnailUrl ? (
                  <>
                    <img 
                      src={thumbnailUrl} 
                      alt="Video thumbnail" 
                      className="thumbnail-image"
                    />
                    <div className="thumbnail-overlay">
                      <span className="material-symbols-outlined thumbnail-icon">play_arrow</span>
                    </div>
                  </>
                ) : (
                  <span className="material-symbols-outlined thumbnail-icon">play_arrow</span>
                )}
              </div>
              <div className="file-details">
                <div className="file-name">{fileInfo.name}</div>
                <div className="file-meta">
                  {fileInfo.duration && <span>{fileInfo.duration}</span>}
                  {fileInfo.duration && fileInfo.size && <span>•</span>}
                  <span>{fileInfo.size}</span>
                </div>
              </div>
              <span className="material-symbols-outlined file-check-icon">check_circle</span>
            </div>
          </div>
        )}

        <input
          ref={fileInputRef}
          type="file"
          accept="video/*"
          multiple={false}
          onChange={handleFileInput}
          style={{ display: 'none' }}
        />
      </div>
    </div>
  );
};

export default VideoSource;
