import React, { useContext, useEffect, useRef } from 'react';
import './App.css';
import { AppProvider, AppContext } from './AppContext';
import { API_BASE_URL } from './api';
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
  // В dev-режиме HMR/hot-reload вызывает beforeunload, что ложно останавливает обработку.
  // Поэтому отправляем stop только при реальном закрытии окна (Electron window.close).
  useEffect(() => {
    // Флаг: true когда окно действительно закрывается (не HMR)
    let isRealUnload = false;

    // Electron main.js шлёт 'window-closing' перед закрытием
    const handleElectronClose = () => { isRealUnload = true; };
    window.addEventListener('electron-window-closing', handleElectronClose);

    // В production (не-webpack) любой unload — реальный
    if (!module.hot) {
      isRealUnload = true;
    }

    const sendStop = () => {
      try {
        const data = JSON.stringify({});
        navigator.sendBeacon(`${API_BASE_URL}/stop`, data);
      } catch (error) {
        // Игнорируем ошибки при закрытии
      }
    };

    const handleBeforeUnload = async (event) => {
      if (!isRealUnload) return;
      sendStop();
      try {
        await fetch(`${API_BASE_URL}/stop`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
          keepalive: true
        });
      } catch (error) {
        // Игнорируем ошибки при закрытии
      }
    };

    window.addEventListener('beforeunload', handleBeforeUnload);

    const handleUnload = () => {
      if (!isRealUnload) return;
      sendStop();
    };

    window.addEventListener('unload', handleUnload);

    return () => {
      window.removeEventListener('electron-window-closing', handleElectronClose);
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
