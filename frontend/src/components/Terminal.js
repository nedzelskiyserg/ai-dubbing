import React, { useState, useEffect, useRef, useCallback } from 'react';
import './Terminal.css';
import { getLogs, clearLogs } from '../api';

const Terminal = () => {
  const [logs, setLogs] = useState([
    '> System initialized...',
    '> Waiting for input...',
    '> Ready.'
  ]);
  const [height, setHeight] = useState(200);
  const [isResizing, setIsResizing] = useState(false);
  const [isUserScrolled, setIsUserScrolled] = useState(false);
  const terminalRef = useRef(null);
  const resizeStartY = useRef(0);
  const resizeStartHeight = useRef(200);
  const isResizingRef = useRef(false);

  // Проверяем, находится ли пользователь внизу консоли
  const checkIfAtBottom = useCallback(() => {
    if (!terminalRef.current) return false;
    const { scrollTop, scrollHeight, clientHeight } = terminalRef.current;
    const threshold = 50; // Порог в пикселях
    return scrollHeight - scrollTop - clientHeight < threshold;
  }, []);

  // Умный скролл: только если пользователь внизу
  useEffect(() => {
    if (terminalRef.current && !isUserScrolled) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
    }
  }, [logs, isUserScrolled]);

  // Отслеживаем прокрутку пользователя
  useEffect(() => {
    const handleScroll = () => {
      if (!terminalRef.current) return;
      const atBottom = checkIfAtBottom();
      setIsUserScrolled(!atBottom);
    };

    const terminal = terminalRef.current;
    if (terminal) {
      terminal.addEventListener('scroll', handleScroll);
      return () => terminal.removeEventListener('scroll', handleScroll);
    }
  }, [checkIfAtBottom]);

  useEffect(() => {
    let retryCount = 0;
    const maxRetries = 3;
    
    // Polling для получения логов
    const interval = setInterval(async () => {
      try {
        const response = await getLogs();
        retryCount = 0; // Сбрасываем счетчик при успехе
        
        if (response && Array.isArray(response) && response.length > 0) {
          // Проверяем, находимся ли мы внизу перед обновлением
          const wasAtBottom = checkIfAtBottom();
          setLogs(response);
          // Если пользователь был внизу, сбрасываем флаг
          if (wasAtBottom) {
            setIsUserScrolled(false);
          }
        } else if (response && Array.isArray(response) && response.length === 0) {
          // Если логов нет, показываем начальное сообщение только если логов действительно нет
          setLogs(prev => {
            if (prev.length === 0 || (prev.length === 1 && prev[0] === '> Terminal cleared.')) {
              return ['> System initialized...', '> Waiting for input...', '> Ready.'];
            }
            return prev;
          });
        }
      } catch (error) {
        // Не логируем ошибку в консоль, если это просто временная проблема подключения
        const isConnectionError = error.code === 'ECONNREFUSED' || 
                                  error.code === 'ERR_NETWORK' || 
                                  error.message?.includes('Network Error') ||
                                  !error.response;
        
        // Показываем ошибку только после нескольких неудачных попыток
        retryCount++;
        if (retryCount >= maxRetries && !isConnectionError) {
          console.error('Failed to fetch logs after multiple attempts:', error);
          // При ошибке показываем сообщение об ошибке только если это не проблема подключения
          setLogs(prev => {
            if (!prev.some(log => log.includes('Failed to fetch'))) {
              return [...prev, `> Error: Failed to fetch logs from API`];
            }
            return prev;
          });
        }
        // Если это ошибка подключения, просто молча игнорируем - API еще не запустился
      }
    }, 2000); // Обновляем каждые 2 секунды

    return () => clearInterval(interval);
  }, [checkIfAtBottom]); // Добавляем checkIfAtBottom в зависимости

  const handleClear = async () => {
    try {
      await clearLogs();
      setLogs(['> Terminal cleared.']);
      setIsUserScrolled(false); // Сбрасываем флаг после очистки
    } catch (error) {
      console.error('Failed to clear logs:', error);
    }
  };

  // Обработчики изменения размера
  const handleResizeMove = useCallback((e) => {
    if (!isResizingRef.current) return;
    
    const deltaY = resizeStartY.current - e.clientY; // Инвертируем, т.к. тянем вверх
    const newHeight = Math.max(200, Math.min(800, resizeStartHeight.current + deltaY));
    setHeight(newHeight);
  }, []);

  const handleResizeEnd = useCallback(() => {
    isResizingRef.current = false;
    setIsResizing(false);
    document.removeEventListener('mousemove', handleResizeMove);
    document.removeEventListener('mouseup', handleResizeEnd);
  }, [handleResizeMove]);

  const handleResizeStart = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    isResizingRef.current = true;
    setIsResizing(true);
    resizeStartY.current = e.clientY;
    resizeStartHeight.current = height;
    document.addEventListener('mousemove', handleResizeMove);
    document.addEventListener('mouseup', handleResizeEnd);
  }, [height, handleResizeMove, handleResizeEnd]);

  useEffect(() => {
    return () => {
      document.removeEventListener('mousemove', handleResizeMove);
      document.removeEventListener('mouseup', handleResizeEnd);
    };
  }, [handleResizeMove, handleResizeEnd]);

  return (
    <div className="terminal-panel" style={{ height: `${height}px` }}>

      <div 
        className={`terminal-resize-handle ${isResizing ? 'resizing' : ''}`}
        onMouseDown={handleResizeStart}
      >
        <div className="resize-handle-line"></div>
      </div>
      
      <div className="terminal-header">
        <div className="terminal-header-left">
          <span className="material-symbols-outlined terminal-icon">terminal</span>
          <span className="terminal-label">[TERMINAL:OUTPUT]</span>
        </div>
        <button className="terminal-clear" onClick={handleClear}>
          <span className="material-symbols-outlined clear-icon">delete</span>
          <span className="clear-text">CLEAR</span>
        </button>
      </div>
      
      <div className="terminal-content" ref={terminalRef}>
        {logs.map((log, index) => (
          <div key={index} className="terminal-line">{log}</div>
        ))}
        <div className="terminal-cursor">
          <span className="cursor-prompt">&gt;</span>
          <span className="cursor-blink">_</span>
        </div>
      </div>
    </div>
  );
};

export default Terminal;
