import React, { useState, useContext } from 'react';
import './AdditionalOptions.css';
import { AppContext } from '../AppContext';
import Dropdown from './Dropdown';

const AdditionalOptions = () => {
  const { options, setOptions } = useContext(AppContext);
  const [processingExpanded, setProcessingExpanded] = useState(true);
  const [translationExpanded, setTranslationExpanded] = useState(true);
  const [voiceExpanded, setVoiceExpanded] = useState(true);

  const updateOption = (key, value) => {
    setOptions(prev => ({ ...prev, [key]: value }));
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
              <div className="checkbox-row">
                <div className="checkbox-container">
                  <div className={`checkbox ${options.diarization ? 'checked' : ''}`} onClick={() => updateOption('diarization', !options.diarization)}>
                    <span className="checkbox-checkmark">✓</span>
                  </div>
                  <span className="checkbox-label" onClick={() => updateOption('diarization', !options.diarization)}>Diarization</span>
                </div>
                <div className="checkbox-container">
                  <div className={`checkbox ${options.transcribe ? 'checked' : ''}`} onClick={() => updateOption('transcribe', !options.transcribe)}>
                    <span className="checkbox-checkmark">✓</span>
                  </div>
                  <span className="checkbox-label" onClick={() => updateOption('transcribe', !options.transcribe)}>Transcribe</span>
                </div>
                <div className="checkbox-container">
                  <div className={`checkbox ${options.cloneVoice ? 'checked' : ''}`} onClick={() => updateOption('cloneVoice', !options.cloneVoice)}>
                    <span className="checkbox-checkmark">✓</span>
                  </div>
                  <span className={`checkbox-label ${!options.cloneVoice ? 'unchecked' : ''}`} onClick={() => updateOption('cloneVoice', !options.cloneVoice)}>Clone voice</span>
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
                      { value: 'GERMAN', label: 'GERMAN' }
                    ]}
                  />
                </div>
                <div className="field-group">
                  <div className="field-label">PROVIDER</div>
                  <Dropdown
                    value={options.provider}
                    onChange={(value) => updateOption('provider', value)}
                    options={[
                      { value: 'QUALITY API', label: 'QUALITY API' },
                      { value: 'Ollama', label: 'Ollama (LLM)' }
                    ]}
                  />
                </div>
              </div>
            </div>
          </div>
          <div className="divider"></div>
        </div>

        {/* VOICE CLONING SECTION */}
        <div className="section">
          <div className="section-header" onClick={() => setVoiceExpanded(!voiceExpanded)}>
            <div className="section-title-group">
              <span className="material-symbols-outlined section-icon voice-icon">record_voice_over</span>
              <span className="section-title">VOICE CLONING</span>
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
                <div className="voice-hint">
                  <span className="hint-label">[!]</span>
                  <span className="hint-text">Python 3.10+ required</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AdditionalOptions;
