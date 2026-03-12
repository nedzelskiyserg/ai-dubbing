import React, { createContext, useState, useEffect, useCallback } from 'react';

export const AppContext = createContext();

// localStorage ключ для API key
const STORAGE_KEY_OPENROUTER = 'ai-dubbing-openrouter-api-key';

export const AppProvider = ({ children }) => {
  const [youtubeUrl, setYoutubeUrl] = useState('');
  const [quality, setQuality] = useState('1080P');
  const [uploadedFile, setUploadedFile] = useState(null);
  const [activePage, setActivePage] = useState('youtube-dubbing');

  // Загружаем сохранённый ключ из localStorage
  const savedKey = localStorage.getItem(STORAGE_KEY_OPENROUTER) || '';

  const [options, setOptions] = useState({
    language: 'AUTO',
    model: 'LARGE',
    speakers: 'AUTO',
    diarization: true,
    transcribe: true,
    cloneVoice: false,
    translate: false,
    targetLang: 'RUSSIAN',
    provider: 'OpenRouter',
    openrouterApiKey: savedKey,
    voiceEnabled: false,
    usePresetVoices: false,
  });

  // Статус валидации ключа: 'idle' | 'checking' | 'valid' | 'invalid'
  const [openrouterKeyStatus, setOpenrouterKeyStatus] = useState(
    savedKey ? 'checking' : 'idle'
  );

  // Валидация ключа через OpenRouter API
  const validateOpenRouterKey = useCallback(async (key) => {
    if (!key || !key.trim()) {
      setOpenrouterKeyStatus('idle');
      return;
    }

    setOpenrouterKeyStatus('checking');

    try {
      const response = await fetch('https://openrouter.ai/api/v1/auth/key', {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${key.trim()}`,
        },
      });

      if (response.ok) {
        setOpenrouterKeyStatus('valid');
        localStorage.setItem(STORAGE_KEY_OPENROUTER, key.trim());
      } else {
        setOpenrouterKeyStatus('invalid');
        localStorage.removeItem(STORAGE_KEY_OPENROUTER);
      }
    } catch {
      setOpenrouterKeyStatus('invalid');
      localStorage.removeItem(STORAGE_KEY_OPENROUTER);
    }
  }, []);

  // Валидируем сохранённый ключ при загрузке
  useEffect(() => {
    if (savedKey) {
      validateOpenRouterKey(savedKey);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <AppContext.Provider
      value={{
        youtubeUrl,
        setYoutubeUrl,
        quality,
        setQuality,
        uploadedFile,
        setUploadedFile,
        activePage,
        setActivePage,
        options,
        setOptions,
        openrouterKeyStatus,
        validateOpenRouterKey,
      }}
    >
      {children}
    </AppContext.Provider>
  );
};
