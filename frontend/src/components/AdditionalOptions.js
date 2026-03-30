import React, { useState, useContext, useEffect, useRef } from 'react';
import './AdditionalOptions.css';
import { AppContext } from '../AppContext';
import Dropdown from './Dropdown';
import PresetsPopup from './PresetsPopup';

const AdditionalOptions = () => {
  const {
    options, setOptions,
    openrouterKeyStatus, validateOpenRouterKey,
    voicerKeyStatus, validateVoicerKey,
    openaiKeyStatus, validateOpenaiKey,
    voicePresets, setVoicePresets,
  } = useContext(AppContext);
  const [processingExpanded, setProcessingExpanded] = useState(true);
  const [translationExpanded, setTranslationExpanded] = useState(true);
  const [voiceExpanded, setVoiceExpanded] = useState(true);
  const [showPresetsPopup, setShowPresetsPopup] = useState(false);
  const debounceRef = useRef(null);
  const voicerDebounceRef = useRef(null);
  const openaiDebounceRef = useRef(null);

  const updateOption = (key, value) => {
    setOptions(prev => ({ ...prev, [key]: value }));
  };

  // Обработчик изменения OpenRouter API ключа с debounce-валидацией
  const handleApiKeyChange = (value) => {
    updateOption('openrouterApiKey', value);

    if (debounceRef.current) clearTimeout(debounceRef.current);

    if (!value || !value.trim()) {
      validateOpenRouterKey('');
      return;
    }

    debounceRef.current = setTimeout(() => {
      validateOpenRouterKey(value);
    }, 600);
  };

  // Обработчик изменения Voicer API ключа с debounce-валидацией
  const handleVoicerKeyChange = (value) => {
    updateOption('voicerApiKey', value);

    if (voicerDebounceRef.current) clearTimeout(voicerDebounceRef.current);

    if (!value || !value.trim()) {
      validateVoicerKey('');
      return;
    }

    voicerDebounceRef.current = setTimeout(() => {
      validateVoicerKey(value);
    }, 600);
  };

  // Обработчик изменения OpenAI API ключа с debounce-валидацией
  const handleOpenaiKeyChange = (value) => {
    updateOption('openaiApiKey', value);

    if (openaiDebounceRef.current) clearTimeout(openaiDebounceRef.current);

    if (!value || !value.trim()) {
      validateOpenaiKey('');
      return;
    }

    openaiDebounceRef.current = setTimeout(() => {
      validateOpenaiKey(value);
    }, 600);
  };

  // Очистка таймеров при размонтировании
  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (voicerDebounceRef.current) clearTimeout(voicerDebounceRef.current);
      if (openaiDebounceRef.current) clearTimeout(openaiDebounceRef.current);
    };
  }, []);

  const keyStatusLabel = {
    idle: '',
    checking: 'CHECKING...',
    valid: 'VALID',
    invalid: 'INVALID KEY',
  };

  const keyStatusClass = {
    idle: '',
    checking: 'status-checking',
    valid: 'status-valid',
    invalid: 'status-invalid',
  };

  return (
    <div className="panel">
      <div className="panel-header">
        <span className="panel-label">[CONFIG:OPTIONS]</span>
        <span className="panel-title">ADDITIONAL OPTIONS</span>
        <span className="material-symbols-outlined panel-icon">tune</span>
      </div>
      <div className="panel-content options-content">
        {/* PROCESSING SECTION */}
        <div className="section">
          <div className="section-header" onClick={() => setProcessingExpanded(!processingExpanded)}>
            <div className="section-title-group">
              <span className="material-symbols-outlined section-icon">settings</span>
              <span className="section-title">PROCESSING</span>
            </div>
            <span className={`section-arrow ${processingExpanded ? 'expanded' : ''}`}>{processingExpanded ? '▼' : '▶'}</span>
          </div>
          <div className={`section-content ${processingExpanded ? 'expanded' : 'collapsed'}`}>
            <div className="section-content-inner">
              <div className="options-row">
                <div className="field-group">
                  <div className="field-label">ENGINE</div>
                  <Dropdown
                    value={options.transcribeEngine}
                    onChange={(value) => updateOption('transcribeEngine', value)}
                    options={[
                      { value: 'whisperx', label: 'WhisperX (local)' },
                      { value: 'openai', label: 'OpenAI (cloud)' }
                    ]}
                  />
                </div>
                <div className="field-group">
                  <div className="field-label">LANGUAGE</div>
                  <Dropdown
                    value={options.language}
                    onChange={(value) => updateOption('language', value)}
                    options={[
                      { value: 'AUTO', label: 'AUTO' },
                      { value: 'RU', label: 'Русский' },
                      { value: 'EN', label: 'Английский' }
                    ]}
                  />
                </div>
                {options.transcribeEngine === 'whisperx' && (
                  <div className="field-group">
                    <div className="field-label">MODEL</div>
                    <Dropdown
                      value={options.model}
                      onChange={(value) => updateOption('model', value)}
                      options={[
                        { value: 'Tiny', label: 'Tiny' },
                        { value: 'Base', label: 'Base' },
                        { value: 'Small', label: 'Small' },
                        { value: 'Medium', label: 'Medium' },
                        { value: 'LARGE', label: 'LARGE' }
                      ]}
                    />
                  </div>
                )}
                <div className="field-group">
                  <div className="field-label">SPEAKERS</div>
                  <Dropdown
                    value={options.speakers}
                    onChange={(value) => updateOption('speakers', value)}
                    options={[
                      { value: 'AUTO', label: 'AUTO' },
                      { value: '1', label: '1' },
                      { value: '2', label: '2' },
                      { value: '3', label: '3' },
                      { value: '4', label: '4' },
                      { value: '5', label: '5' }
                    ]}
                  />
                </div>
              </div>
              {options.transcribeEngine === 'openai' && (
                <div className="options-row api-key-row">
                  <div className="field-group full-width">
                    <div className="field-label-row">
                      <div className="field-label">OPENAI API KEY</div>
                      {openaiKeyStatus !== 'idle' && (
                        <span className={`key-status ${keyStatusClass[openaiKeyStatus]}`}>
                          {keyStatusLabel[openaiKeyStatus]}
                        </span>
                      )}
                    </div>
                    <input
                      type="password"
                      className={`api-key-input ${openaiKeyStatus === 'valid' ? 'input-valid' : ''} ${openaiKeyStatus === 'invalid' ? 'input-invalid' : ''}`}
                      value={options.openaiApiKey || ''}
                      onChange={(e) => handleOpenaiKeyChange(e.target.value)}
                      placeholder="sk-proj-..."
                    />
                  </div>
                </div>
              )}
              <div className="checkbox-row">
                {options.transcribeEngine === 'whisperx' && (
                  <div className="checkbox-container">
                    <div className={`checkbox ${options.diarization ? 'checked' : ''}`} onClick={() => updateOption('diarization', !options.diarization)}>
                      <span className="checkbox-checkmark">✓</span>
                    </div>
                    <span className="checkbox-label" onClick={() => updateOption('diarization', !options.diarization)}>Diarization</span>
                  </div>
                )}
                <div className="checkbox-container">
                  <div className={`checkbox ${options.transcribe ? 'checked' : ''}`} onClick={() => updateOption('transcribe', !options.transcribe)}>
                    <span className="checkbox-checkmark">✓</span>
                  </div>
                  <span className="checkbox-label" onClick={() => updateOption('transcribe', !options.transcribe)}>Transcribe</span>
                </div>
              </div>
            </div>
          </div>
          <div className="divider"></div>
        </div>

        {/* TRANSLATION SECTION */}
        <div className="section">
          <div className="section-header" onClick={() => setTranslationExpanded(!translationExpanded)}>
            <div className="section-title-group">
              <span className="material-symbols-outlined section-icon">translate</span>
              <span className="section-title">TRANSLATION</span>
            </div>
            <span className={`section-arrow ${translationExpanded ? 'expanded' : ''}`}>{translationExpanded ? '▼' : '▶'}</span>
          </div>
          <div className={`section-content ${translationExpanded ? 'expanded' : 'collapsed'}`}>
            <div className="section-content-inner">
              <div className="options-row">
                <div className="checkbox-container">
                  <div className={`checkbox ${options.translate ? 'checked' : ''}`} onClick={() => updateOption('translate', !options.translate)}>
                    <span className="checkbox-checkmark">✓</span>
                  </div>
                  <span className="checkbox-label" onClick={() => updateOption('translate', !options.translate)}>Enable</span>
                </div>
                <div className="field-group">
                  <div className="field-label">TARGET</div>
                  <Dropdown
                    value={options.targetLang}
                    onChange={(value) => updateOption('targetLang', value)}
                    options={[
                      { value: 'RUSSIAN', label: 'RUSSIAN' },
                      { value: 'ENGLISH', label: 'ENGLISH' },
                      { value: 'SPANISH', label: 'SPANISH' },
                      { value: 'FRENCH', label: 'FRENCH' },
                      { value: 'GERMAN', label: 'GERMAN' },
                      { value: 'ITALIAN', label: 'ITALIAN' },
                      { value: 'PORTUGUESE', label: 'PORTUGUESE' },
                      { value: 'CHINESE', label: 'CHINESE' },
                      { value: 'JAPANESE', label: 'JAPANESE' },
                      { value: 'KOREAN', label: 'KOREAN' },
                      { value: 'ARABIC', label: 'ARABIC' },
                      { value: 'HINDI', label: 'HINDI' },
                      { value: 'POLISH', label: 'POLISH' },
                      { value: 'TURKISH', label: 'TURKISH' },
                      { value: 'DUTCH', label: 'DUTCH' },
                      { value: 'CZECH', label: 'CZECH' },
                      { value: 'HUNGARIAN', label: 'HUNGARIAN' },
                      { value: 'SWEDISH', label: 'SWEDISH' },
                      { value: 'NORWEGIAN', label: 'NORWEGIAN' }
                    ]}
                  />
                </div>
                <div className="field-group">
                  <div className="field-label">PROVIDER</div>
                  <Dropdown
                    value={options.provider}
                    onChange={(value) => updateOption('provider', value)}
                    options={[
                      { value: 'OpenRouter', label: 'OpenRouter (Gemini 3.0 Flash)' },
                      { value: 'Ollama', label: 'Ollama (LLM)' },
                      { value: 'QUALITY API', label: 'QUALITY API' }
                    ]}
                  />
                </div>
              </div>
              {options.provider === 'OpenRouter' && (
                <div className="options-row api-key-row">
                  <div className="field-group full-width">
                    <div className="field-label-row">
                      <div className="field-label">OPENROUTER API KEY</div>
                      {openrouterKeyStatus !== 'idle' && (
                        <span className={`key-status ${keyStatusClass[openrouterKeyStatus]}`}>
                          {keyStatusLabel[openrouterKeyStatus]}
                        </span>
                      )}
                    </div>
                    <input
                      type="password"
                      className={`api-key-input ${openrouterKeyStatus === 'valid' ? 'input-valid' : ''} ${openrouterKeyStatus === 'invalid' ? 'input-invalid' : ''}`}
                      value={options.openrouterApiKey || ''}
                      onChange={(e) => handleApiKeyChange(e.target.value)}
                      placeholder="sk-or-..."
                    />
                  </div>
                </div>
              )}
            </div>
          </div>
          <div className="divider"></div>
        </div>

        {/* DUBBING SECTION (Voicer API only) */}
        <div className="section">
          <div className="section-header" onClick={() => setVoiceExpanded(!voiceExpanded)}>
            <div className="section-title-group">
              <span className="material-symbols-outlined section-icon voice-icon">record_voice_over</span>
              <span className="section-title">DUBBING</span>
            </div>
            <span className={`section-arrow ${voiceExpanded ? 'expanded' : ''}`}>{voiceExpanded ? '▼' : '▶'}</span>
          </div>
          <div className={`section-content ${voiceExpanded ? 'expanded' : 'collapsed'}`}>
            <div className="section-content-inner">
              <div className="options-row">
                <div className="checkbox-container">
                  <div className={`checkbox ${options.voiceEnabled ? 'checked' : ''}`} onClick={() => updateOption('voiceEnabled', !options.voiceEnabled)}>
                    <span className="checkbox-checkmark">✓</span>
                  </div>
                  <span className={`checkbox-label ${!options.voiceEnabled ? 'unchecked' : ''}`} onClick={() => updateOption('voiceEnabled', !options.voiceEnabled)}>Enable dubbing</span>
                </div>
                <button
                  className="manage-presets-btn"
                  onClick={() => setShowPresetsPopup(true)}
                >
                  <span className="material-symbols-outlined">playlist_add</span>
                  VOICE PRESETS
                  <span className="presets-count">{voicePresets.length}</span>
                </button>
              </div>
              <div className="options-row api-key-row">
                <div className="field-group full-width">
                  <div className="field-label-row">
                    <div className="field-label">VOICER API KEY</div>
                    {voicerKeyStatus !== 'idle' && (
                      <span className={`key-status ${keyStatusClass[voicerKeyStatus]}`}>
                        {keyStatusLabel[voicerKeyStatus]}
                      </span>
                    )}
                  </div>
                  <input
                    type="password"
                    className={`api-key-input ${voicerKeyStatus === 'valid' ? 'input-valid' : ''} ${voicerKeyStatus === 'invalid' ? 'input-invalid' : ''}`}
                    value={options.voicerApiKey || ''}
                    onChange={(e) => handleVoicerKeyChange(e.target.value)}
                    placeholder="voicer-api-key..."
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Presets Popup */}
      {showPresetsPopup && (
        <PresetsPopup
          presets={voicePresets}
          onSave={(updated) => setVoicePresets(updated)}
          onClose={() => setShowPresetsPopup(false)}
        />
      )}
    </div>
  );
};

export default AdditionalOptions;
