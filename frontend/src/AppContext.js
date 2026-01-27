import React, { createContext, useState } from 'react';

export const AppContext = createContext();

export const AppProvider = ({ children }) => {
  const [youtubeUrl, setYoutubeUrl] = useState('');
  const [quality, setQuality] = useState('1080P');
  const [uploadedFile, setUploadedFile] = useState(null);
  const [activePage, setActivePage] = useState('youtube-dubbing');
  const [options, setOptions] = useState({
    language: 'AUTO',
    model: 'LARGE',
    speakers: 'AUTO',
    diarization: true,
    transcribe: true,
    cloneVoice: false,
    translate: false,
    targetLang: 'RUSSIAN',
    provider: 'QUALITY API',
    voiceEnabled: false,
  });

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
      }}
    >
      {children}
    </AppContext.Provider>
  );
};
