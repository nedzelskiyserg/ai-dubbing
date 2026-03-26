import React, { createContext, useState, useEffect, useCallback } from 'react';

export const AppContext = createContext();

// localStorage ключи
const STORAGE_KEY_OPENROUTER = 'ai-dubbing-openrouter-api-key';
const STORAGE_KEY_VOICER = 'ai-dubbing-voicer-api-key';
const STORAGE_KEY_OPENAI = 'ai-dubbing-openai-api-key';
const STORAGE_KEY_PRESETS = 'ai-dubbing-voice-presets';
const STORAGE_KEY_OPTIONS = 'ai-dubbing-options';

export const AppProvider = ({ children }) => {
  const [youtubeUrl, setYoutubeUrl] = useState('');
  const [quality, setQuality] = useState('1080P');
  const [uploadedFile, setUploadedFile] = useState(null);
  const [activePage, setActivePage] = useState('youtube-dubbing');

  // Загружаем сохранённые данные из localStorage
  const savedKey = localStorage.getItem(STORAGE_KEY_OPENROUTER) || '';
  const savedVoicerKey = localStorage.getItem(STORAGE_KEY_VOICER) || '';
  const savedOpenaiKey = localStorage.getItem(STORAGE_KEY_OPENAI) || '';

  const loadPresets = () => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY_PRESETS);
      return raw ? JSON.parse(raw) : [];
    } catch {
      return [];
    }
  };

  const [voicePresets, setVoicePresets] = useState(loadPresets);

  // Сохраняем пресеты в localStorage при изменении
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY_PRESETS, JSON.stringify(voicePresets));
  }, [voicePresets]);

  // Загружаем сохранённые опции из localStorage
  const loadOptions = () => {
    const defaults = {
      language: 'AUTO',
      model: 'LARGE',
      speakers: 'AUTO',
      diarization: true,
      transcribe: true,
      transcribeEngine: 'whisperx',
      openaiApiKey: savedOpenaiKey,
      translate: false,
      targetLang: 'RUSSIAN',
      provider: 'OpenRouter',
      openrouterApiKey: savedKey,
      voiceEnabled: false,
      voicerApiKey: savedVoicerKey,
    };
    try {
      const raw = localStorage.getItem(STORAGE_KEY_OPTIONS);
      if (raw) {
        const saved = JSON.parse(raw);
        // Мержим сохранённые поверх дефолтов, а ключи берём свежие из localStorage
        return {
          ...defaults,
          ...saved,
          openrouterApiKey: savedKey,
          voicerApiKey: savedVoicerKey,
          openaiApiKey: savedOpenaiKey,
        };
      }
    } catch { /* ignore */ }
    return defaults;
  };

  const [options, setOptions] = useState(loadOptions);

  // Сохраняем опции в localStorage при изменении (кроме API-ключей — они хранятся отдельно)
  useEffect(() => {
    const { openrouterApiKey, voicerApiKey, openaiApiKey, ...safeOptions } = options;
    localStorage.setItem(STORAGE_KEY_OPTIONS, JSON.stringify(safeOptions));
  }, [options]);

  // Статус валидации ключей: 'idle' | 'checking' | 'valid' | 'invalid'
  const [openrouterKeyStatus, setOpenrouterKeyStatus] = useState(
    savedKey ? 'checking' : 'idle'
  );
  const [voicerKeyStatus, setVoicerKeyStatus] = useState(
    savedVoicerKey ? 'checking' : 'idle'
  );
  const [openaiKeyStatus, setOpenaiKeyStatus] = useState(
    savedOpenaiKey ? 'checking' : 'idle'
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

  // Валидация ключа OpenAI (GET /v1/models)
  const validateOpenaiKey = useCallback(async (key) => {
    if (!key || !key.trim()) {
      setOpenaiKeyStatus('idle');
      return;
    }

    setOpenaiKeyStatus('checking');

    try {
      const response = await fetch('https://api.openai.com/v1/models', {
        method: 'GET',
        headers: { 'Authorization': `Bearer ${key.trim()}` },
      });

      if (response.ok) {
        setOpenaiKeyStatus('valid');
        localStorage.setItem(STORAGE_KEY_OPENAI, key.trim());
      } else {
        setOpenaiKeyStatus('invalid');
        localStorage.removeItem(STORAGE_KEY_OPENAI);
      }
    } catch {
      setOpenaiKeyStatus('invalid');
      localStorage.removeItem(STORAGE_KEY_OPENAI);
    }
  }, []);

  // Валидация ключа через Voicer API (GET /balance)
  const validateVoicerKey = useCallback(async (key) => {
    if (!key || !key.trim()) {
      setVoicerKeyStatus('idle');
      return;
    }

    setVoicerKeyStatus('checking');

    try {
      const response = await fetch('https://voiceapi.csv666.ru/balance', {
        method: 'GET',
        headers: {
          'X-API-Key': key.trim(),
        },
      });

      if (response.ok) {
        setVoicerKeyStatus('valid');
        localStorage.setItem(STORAGE_KEY_VOICER, key.trim());
      } else {
        setVoicerKeyStatus('invalid');
        localStorage.removeItem(STORAGE_KEY_VOICER);
      }
    } catch {
      setVoicerKeyStatus('invalid');
      localStorage.removeItem(STORAGE_KEY_VOICER);
    }
  }, []);

  // Валидируем сохранённые ключи при загрузке
  useEffect(() => {
    if (savedKey) {
      validateOpenRouterKey(savedKey);
    }
    if (savedVoicerKey) {
      validateVoicerKey(savedVoicerKey);
    }
    if (savedOpenaiKey) {
      validateOpenaiKey(savedOpenaiKey);
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
        voicerKeyStatus,
        validateVoicerKey,
        openaiKeyStatus,
        validateOpenaiKey,
        voicePresets,
        setVoicePresets,
      }}
    >
      {children}
    </AppContext.Provider>
  );
};
