import React, { useContext, useState, useEffect, useRef } from 'react';
import './StartButton.css';
import { processYouTube, processFile, getStatus, stopProcessing, healthCheck } from '../api';
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
      // Проверяем доступность API сервера перед запуском (с повторными попытками)
      try {
        await healthCheck(5, 1000); // 5 попыток с интервалом 1 секунда
      } catch (healthError) {
        console.error('API server is not available:', healthError);
        alert('Ошибка: API сервер не отвечает.\n\n' +
              'Возможные причины:\n' +
              '1. API сервер еще запускается (подождите несколько секунд)\n' +
              '2. Порт 5001 занят другим приложением\n' +
              '3. Python backend не запустился корректно\n\n' +
              'Попробуйте перезапустить приложение.');
        setIsProcessing(false);
        setProgress(0);
        return;
      }

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
      
      // Формируем информативное сообщение об ошибке
      let errorMessage = 'Ошибка запуска обработки';
      
      if (error.response) {
        // Ошибка от сервера
        const status = error.response.status;
        const data = error.response.data;
        
        if (status === 500) {
          errorMessage = 'Внутренняя ошибка сервера.\n\nПроверьте логи в терминале для деталей.';
        } else if (status === 400) {
          errorMessage = data?.error || 'Неверный запрос. Проверьте параметры.';
        } else if (status === 503) {
          errorMessage = 'Сервер временно недоступен.\n\nВозможно, идет обработка другого запроса.';
        } else {
          errorMessage = `Ошибка сервера (код ${status}).\n\n${data?.error || error.message}`;
        }
      } else if (error.request) {
        // Запрос отправлен, но ответа нет
        errorMessage = 'Сервер не отвечает.\n\nПроверьте, что API сервер запущен и доступен на порту 5001.';
      } else if (error.code === 'ECONNREFUSED' || error.code === 'ERR_NETWORK') {
        errorMessage = 'Не удалось подключиться к API серверу.\n\nУбедитесь, что приложение запущено корректно.';
      } else if (error.message) {
        errorMessage = `Ошибка: ${error.message}`;
      }
      
      alert(errorMessage);
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
