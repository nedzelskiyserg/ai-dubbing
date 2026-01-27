import React, { useContext, useState, useEffect, useRef } from 'react';
import './StartButton.css';
import { processYouTube, processFile, getStatus, stopProcessing } from '../api';
import { AppContext } from '../AppContext';

const StartButton = () => {
  const { youtubeUrl, quality, uploadedFile, options } = useContext(AppContext);
  const [isProcessing, setIsProcessing] = useState(false);
  const [progress, setProgress] = useState(0);
  const statusIntervalRef = useRef(null);

  useEffect(() => {
    // Очистка интервала при размонтировании
    return () => {
      if (statusIntervalRef.current) {
        clearInterval(statusIntervalRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (isProcessing) {
      // Опрашиваем статус каждую секунду
      statusIntervalRef.current = setInterval(async () => {
        try {
          const status = await getStatus();
          setProgress(status.progress || 0);
          if (!status.is_processing) {
            setIsProcessing(false);
            setProgress(0);
            if (statusIntervalRef.current) {
              clearInterval(statusIntervalRef.current);
            }
          }
        } catch (error) {
          console.error('Error getting status:', error);
        }
      }, 1000);
    } else {
      if (statusIntervalRef.current) {
        clearInterval(statusIntervalRef.current);
        statusIntervalRef.current = null;
      }
      setProgress(0);
    }
  }, [isProcessing]);

  const handleStart = async () => {
    if (isProcessing) return;

    // Проверяем наличие источника
    const hasSource = uploadedFile || (youtubeUrl && youtubeUrl.trim() && youtubeUrl !== 'https://youtube.com/watch?v=...');
    
    if (!hasSource) {
      alert('Пожалуйста, выберите видео (YouTube URL или файл)');
      return;
    }

    setIsProcessing(true);
    setProgress(0);
    try {
      const processOptions = {
        language: options.language,
        model: options.model,
        speakers: options.speakers,
        diarization: options.diarization,
        transcribe: options.transcribe,
        translate: options.translate,
        target_lang: options.targetLang,
        provider: options.provider === 'QUALITY API' ? 'api' : 'ollama',
        voice_cloning: options.voiceEnabled,
      };

      if (uploadedFile) {
        await processFile(uploadedFile, processOptions);
      } else if (youtubeUrl && youtubeUrl.trim() && youtubeUrl !== 'https://youtube.com/watch?v=...') {
        await processYouTube(youtubeUrl, quality, processOptions);
      }
    } catch (error) {
      console.error('Error starting processing:', error);
      alert('Ошибка запуска обработки');
      setIsProcessing(false);
      setProgress(0);
    }
  };

  const handleStop = async (e) => {
    e.stopPropagation();
    
    // Немедленно очищаем интервал опроса статуса
    if (statusIntervalRef.current) {
      clearInterval(statusIntervalRef.current);
      statusIntervalRef.current = null;
    }
    
    // Немедленно обновляем UI
    setIsProcessing(false);
    setProgress(0);
    
    try {
      await stopProcessing();
    } catch (error) {
      console.error('Error stopping processing:', error);
      // Даже если запрос не удался, UI уже обновлен
    }
  };

  return (
    <button 
      className={`start-button ${isProcessing ? 'processing' : ''}`} 
      onClick={isProcessing ? handleStop : handleStart}
      disabled={false}
    >
      <div className="start-button-content">
        {isProcessing ? (
          <>
            <span className="material-symbols-outlined start-icon">stop</span>
            <span className="start-text">STOP</span>
          </>
        ) : (
          <>
            <span className="material-symbols-outlined start-icon">play_arrow</span>
            <span className="start-text">START PROCESSING</span>
          </>
        )}
      </div>
      {isProcessing && (
        <>
          <span className="progress-text">{Math.round(progress)}%</span>
          <div className="progress-bar-container">
            <div 
              className="progress-bar-fill" 
              style={{ width: `${progress}%` }}
            />
          </div>
        </>
      )}
    </button>
  );
};

export default StartButton;
