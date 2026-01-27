import React, { useContext, useEffect, useRef } from 'react';
import './App.css';
import { AppProvider, AppContext } from './AppContext';
import Header from './components/Header';
import Tabs from './components/Tabs';
import VideoSource from './components/VideoSource';
import AdditionalOptions from './components/AdditionalOptions';
import StartButton from './components/StartButton';
import Terminal from './components/Terminal';

const AppContent = () => {
  const { activePage } = useContext(AppContext);
  const contentWrapperRef = useRef(null);

  useEffect(() => {
    const contentWrapper = contentWrapperRef.current;
    if (!contentWrapper) return;

    let scrollTimeout = null;
    let rafId = null;

    const handleScroll = () => {
      // Отменяем предыдущий RAF если есть
      if (rafId) {
        cancelAnimationFrame(rafId);
      }
      
      // Используем requestAnimationFrame для плавного добавления класса
      rafId = requestAnimationFrame(() => {
        if (!contentWrapper.classList.contains('scrolling')) {
          contentWrapper.classList.add('scrolling');
        }
      });
      
      // Очищаем предыдущий таймер
      if (scrollTimeout) {
        clearTimeout(scrollTimeout);
      }
      
      // Убираем класс через 1.5 секунды после окончания скролла
      scrollTimeout = setTimeout(() => {
        contentWrapper.classList.remove('scrolling');
      }, 1500);
    };

    contentWrapper.addEventListener('scroll', handleScroll, { passive: true });

    return () => {
      contentWrapper.removeEventListener('scroll', handleScroll);
      if (scrollTimeout) {
        clearTimeout(scrollTimeout);
      }
      if (rafId) {
        cancelAnimationFrame(rafId);
      }
    };
  }, []);

  // Обработка закрытия окна - принудительно останавливаем все процессы
  useEffect(() => {
    const handleBeforeUnload = async (event) => {
      // Отправляем запрос на остановку процесса
      try {
        // Используем sendBeacon для надежной отправки даже при закрытии
        const data = JSON.stringify({});
        navigator.sendBeacon('http://localhost:5001/api/stop', data);
        
        // Также пытаемся отправить через fetch (fallback)
        try {
          await fetch('http://localhost:5001/api/stop', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: data,
            keepalive: true // Важно для отправки при закрытии
          });
        } catch (error) {
          // Игнорируем ошибки при закрытии
        }
      } catch (error) {
        // Игнорируем ошибки при закрытии
      }
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    
    // Также обрабатываем событие unload
    const handleUnload = () => {
      try {
        const data = JSON.stringify({});
        navigator.sendBeacon('http://localhost:5001/api/stop', data);
      } catch (error) {
        // Игнорируем ошибки
      }
    };
    
    window.addEventListener('unload', handleUnload);

    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload);
      window.removeEventListener('unload', handleUnload);
    };
  }, []);

  return (
    <div className="app">
      <Header />
      <Tabs />
      <div className="main-content">
        <div className="content-wrapper" ref={contentWrapperRef}>
          <div className="content-inner">
            <div 
              key="youtube-dubbing"
              className={`page-content ${activePage === 'youtube-dubbing' ? 'page-active' : 'page-inactive'}`}
            >
              <VideoSource />
              <AdditionalOptions />
              <StartButton />
            </div>
            <div 
              key="shorts-generator"
              className={`page-content ${activePage === 'shorts-generator' ? 'page-active' : 'page-inactive'}`}
            >
              <div className="shorts-generator-placeholder">
                <div className="placeholder-content">
                  <h2>SHORTS GENERATOR</h2>
                  <p>Скоро здесь будет функционал генерации шортсов</p>
                </div>
              </div>
            </div>
          </div>
        </div>
        <Terminal />
      </div>
    </div>
  );
};

function App() {
  return (
    <AppProvider>
      <AppContent />
    </AppProvider>
  );
}

export default App;
