import React, { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import { createPortal } from 'react-dom';
import './PresetsPopup.css';

const LANGUAGES = ['ALL', 'RUSSIAN', 'ENGLISH', 'SPANISH', 'FRENCH', 'GERMAN'];

const emptyPreset = () => ({
  id: Date.now().toString(),
  name: '',
  templateId: '',
  description: '',
  languages: [],
  langPriority: {}, // { RUSSIAN: 0, ENGLISH: 1, ... }
});

// Ensure every preset has langPriority for each of its languages
const ensureLangPriorities = (presets) => {
  // For each language, find which presets belong to it and assign priorities
  // if they don't already have one
  const langGroups = {};
  LANGUAGES.filter(l => l !== 'ALL').forEach(lang => {
    langGroups[lang] = presets
      .filter(p => p.languages.includes(lang))
      .sort((a, b) => {
        const pa = a.langPriority?.[lang] ?? Infinity;
        const pb = b.langPriority?.[lang] ?? Infinity;
        return pa - pb;
      });
  });

  return presets.map(p => {
    const newPriority = { ...(p.langPriority || {}) };
    p.languages.forEach(lang => {
      if (newPriority[lang] === undefined) {
        const group = langGroups[lang];
        const idx = group.findIndex(g => g.id === p.id);
        newPriority[lang] = idx >= 0 ? idx : group.length;
      }
    });
    // Clean up priorities for languages this preset no longer belongs to
    Object.keys(newPriority).forEach(lang => {
      if (!p.languages.includes(lang)) {
        delete newPriority[lang];
      }
    });
    return { ...p, langPriority: newPriority };
  });
};

const PresetsPopup = ({ presets, onSave, onClose }) => {
  const [localPresets, setLocalPresets] = useState(() => ensureLangPriorities(presets.map(p => ({ ...p }))));
  const [activeTab, setActiveTab] = useState('ALL');
  const [editingPreset, setEditingPreset] = useState(null);
  const [isNew, setIsNew] = useState(false);
  const [dragState, setDragState] = useState(null);
  const [confirmExit, setConfirmExit] = useState(false);
  const fileInputRef = useRef(null);
  const listRef = useRef(null);
  const cardRefs = useRef({});  // keyed by preset id
  const justFinishedDrag = useRef(false);

  const isLangTab = activeTab !== 'ALL';

  // ── Filtered & sorted presets for current tab ──────────
  const displayPresets = useMemo(() => {
    if (activeTab === 'ALL') return localPresets;
    return localPresets
      .filter(p => p.languages.includes(activeTab))
      .sort((a, b) => {
        const pa = a.langPriority?.[activeTab] ?? Infinity;
        const pb = b.langPriority?.[activeTab] ?? Infinity;
        return pa - pb;
      });
  }, [localPresets, activeTab]);

  // ── Dirty check ────────────────────────────────────────
  const isDirty = useCallback(() => {
    if (localPresets.length !== presets.length) return true;
    return localPresets.some((p, i) => {
      const orig = presets[i];
      if (!orig) return true;
      return p.id !== orig.id
        || p.name !== orig.name
        || p.templateId !== orig.templateId
        || p.description !== orig.description
        || JSON.stringify(p.languages) !== JSON.stringify(orig.languages)
        || JSON.stringify(p.langPriority) !== JSON.stringify(orig.langPriority);
    });
  }, [localPresets, presets]);

  // ── Safe close ─────────────────────────────────────────
  const requestClose = useCallback(() => {
    if (isDirty()) {
      setConfirmExit(true);
    } else {
      onClose();
    }
  }, [isDirty, onClose]);

  const handleConfirmSaveAndExit = () => { onSave(localPresets); onClose(); };
  const handleConfirmDiscard = () => { onClose(); };
  const handleConfirmCancel = () => { setConfirmExit(false); };

  // ── Overlay click ──────────────────────────────────────
  const handleOverlayClick = useCallback((e) => {
    if (justFinishedDrag.current) { justFinishedDrag.current = false; return; }
    if (dragState) return;
    if (e.target === e.currentTarget) requestClose();
  }, [dragState, requestClose]);

  // ── Pointer-based Drag & Drop (language tabs only) ─────
  const handlePointerDown = useCallback((e, displayIndex) => {
    if (!e.target.closest('.drag-handle')) return;
    e.preventDefault();
    e.stopPropagation();

    const presetId = displayPresets[displayIndex]?.id;
    if (!presetId) return;
    const card = cardRefs.current[presetId];
    if (!card) return;

    const rect = card.getBoundingClientRect();
    const cardHeight = rect.height + 12;

    setDragState({
      displayIndex,
      startY: e.clientY,
      currentY: e.clientY,
      cardHeight,
    });
  }, [displayPresets]);

  useEffect(() => {
    if (!dragState) return;

    const handlePointerMove = (e) => {
      e.preventDefault();
      setDragState(prev => prev ? { ...prev, currentY: e.clientY } : null);
    };

    const handlePointerUp = (e) => {
      e.preventDefault();
      e.stopPropagation();
      justFinishedDrag.current = true;
      setTimeout(() => { justFinishedDrag.current = false; }, 50);

      setDragState(prev => {
        if (!prev) return null;
        const delta = prev.currentY - prev.startY;
        const indexShift = Math.round(delta / prev.cardHeight);
        if (indexShift !== 0) {
          // Reorder within the current language tab
          const lang = activeTab;
          const sorted = [...displayPresets];
          const newIndex = Math.max(0, Math.min(sorted.length - 1, prev.displayIndex + indexShift));
          const [dragged] = sorted.splice(prev.displayIndex, 1);
          sorted.splice(newIndex, 0, dragged);

          // Update langPriority for this language on all affected presets
          const newOrder = {};
          sorted.forEach((p, i) => { newOrder[p.id] = i; });

          setLocalPresets(items => items.map(p => {
            if (newOrder[p.id] !== undefined) {
              return { ...p, langPriority: { ...p.langPriority, [lang]: newOrder[p.id] } };
            }
            return p;
          }));
        }
        return null;
      });
    };

    window.addEventListener('pointermove', handlePointerMove, { passive: false });
    window.addEventListener('pointerup', handlePointerUp);
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'grabbing';

    return () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
    };
  }, [dragState !== null]); // eslint-disable-line react-hooks/exhaustive-deps

  // Calculate card transform during drag
  const getCardStyle = useCallback((displayIndex) => {
    if (!dragState) return {};

    const delta = dragState.currentY - dragState.startY;
    const indexShift = Math.round(delta / dragState.cardHeight);

    if (displayIndex === dragState.displayIndex) {
      return {
        transform: `translateY(${delta}px) scale(1.02)`,
        zIndex: 10,
        boxShadow: '0 8px 32px rgba(0, 0, 0, 0.5), 0 0 0 1px rgba(255, 214, 0, 0.2)',
        transition: 'box-shadow 0.15s, transform 0s',
        position: 'relative',
      };
    }

    const draggedIdx = dragState.displayIndex;
    const targetIdx = draggedIdx + indexShift;
    const clampedTarget = Math.max(0, Math.min(displayPresets.length - 1, targetIdx));

    let shift = 0;
    if (draggedIdx < clampedTarget && displayIndex > draggedIdx && displayIndex <= clampedTarget) {
      shift = -dragState.cardHeight;
    } else if (draggedIdx > clampedTarget && displayIndex >= clampedTarget && displayIndex < draggedIdx) {
      shift = dragState.cardHeight;
    }

    return {
      transform: shift ? `translateY(${shift}px)` : 'translateY(0)',
      transition: 'transform 0.2s cubic-bezier(0.2, 0, 0, 1)',
    };
  }, [dragState, displayPresets.length]);

  // ── CRUD ─────────────────────────────────────────────
  const handleAdd = () => { setEditingPreset(emptyPreset()); setIsNew(true); };

  const handleEdit = (preset) => { setEditingPreset({ ...preset }); setIsNew(false); };

  const handleDelete = (id) => {
    setDragState(null);
    setLocalPresets(prev => {
      const updated = prev.filter(p => p.id !== id);
      return ensureLangPriorities(updated);
    });
    delete cardRefs.current[id];
  };

  const handleFormSave = () => {
    if (!editingPreset.name.trim() || !editingPreset.templateId.trim()) return;
    if (isNew) {
      setLocalPresets(prev => ensureLangPriorities([...prev, editingPreset]));
    } else {
      setLocalPresets(prev => ensureLangPriorities(prev.map(p => p.id === editingPreset.id ? editingPreset : p)));
    }
    setEditingPreset(null);
    setIsNew(false);
  };

  const handleFormCancel = () => { setEditingPreset(null); setIsNew(false); };

  const updateField = (field, value) => {
    setEditingPreset(prev => ({ ...prev, [field]: value }));
  };

  const toggleLanguage = (lang) => {
    setEditingPreset(prev => ({
      ...prev,
      languages: prev.languages.includes(lang)
        ? prev.languages.filter(l => l !== lang)
        : [...prev.languages, lang],
    }));
  };

  // ── Import / Export ──────────────────────────────────
  const handleExport = useCallback(() => {
    const json = JSON.stringify(localPresets, null, 2);
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'voice-presets.json';
    a.click();
    URL.revokeObjectURL(url);
  }, [localPresets]);

  const handleImport = useCallback(() => { fileInputRef.current?.click(); }, []);

  const handleFileChange = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const imported = JSON.parse(ev.target.result);
        if (Array.isArray(imported)) {
          setLocalPresets(prev => {
            const existingTemplateIds = new Set(prev.map(p => p.templateId));
            const newPresets = imported
              .filter(p => p.templateId && !existingTemplateIds.has(p.templateId))
              .map(p => ({
                ...p,
                id: Date.now().toString() + Math.random().toString(36).slice(2),
              }));
            if (newPresets.length === 0) return prev;
            return ensureLangPriorities([...prev, ...newPresets]);
          });
        }
      } catch {
        alert('Invalid JSON file');
      }
    };
    reader.readAsText(file);
    e.target.value = '';
  };

  const handleSaveAll = () => { onSave(localPresets); onClose(); };

  // ── Keyboard: Escape ─────────────────────────────────
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        if (confirmExit) setConfirmExit(false);
        else if (editingPreset) handleFormCancel();
        else requestClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [confirmExit, editingPreset, requestClose]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Render: Exit Confirmation Dialog ──────────────────
  const renderConfirmDialog = () => {
    if (!confirmExit) return null;
    return createPortal(
      <div className="presets-overlay confirm-overlay" onClick={(e) => { if (e.target === e.currentTarget) setConfirmExit(false); }}>
        <div className="confirm-dialog" onClick={e => e.stopPropagation()}>
          <div className="confirm-icon-row">
            <span className="material-symbols-outlined confirm-icon">warning</span>
          </div>
          <div className="confirm-title">UNSAVED CHANGES</div>
          <div className="confirm-message">You have unsaved changes to your voice presets. What would you like to do?</div>
          <div className="confirm-buttons">
            <button className="btn-secondary" onClick={handleConfirmCancel}>BACK</button>
            <button className="btn-discard" onClick={handleConfirmDiscard}>DISCARD</button>
            <button className="btn-primary" onClick={handleConfirmSaveAndExit}>
              SAVE & EXIT
              <span className="material-symbols-outlined btn-icon">check</span>
            </button>
          </div>
        </div>
      </div>,
      document.body
    );
  };

  // ── Render: Add/Edit Form ────────────────────────────
  if (editingPreset) {
    return createPortal(
      <div className="presets-overlay" onClick={(e) => { if (e.target === e.currentTarget) handleFormCancel(); }}>
        <div className="presets-popup" onClick={e => e.stopPropagation()}>
          <div className="presets-header">
            <div className="presets-title-group">
              <span className="material-symbols-outlined presets-icon">
                {isNew ? 'add_circle' : 'edit'}
              </span>
              <span className="presets-title">{isNew ? 'ADD NEW PRESET' : 'EDIT PRESET'}</span>
            </div>
            <button className="presets-close-btn" onClick={handleFormCancel}>
              <span className="material-symbols-outlined">close</span>
            </button>
          </div>

          <div className="presets-form">
            <div className="form-row-2col">
              <div className="form-field">
                <label className="form-label">PRESET NAME</label>
                <input className="form-input" value={editingPreset.name}
                  onChange={e => updateField('name', e.target.value)} placeholder="e.g. Adam Narrator" />
              </div>
              <div className="form-field">
                <label className="form-label">TEMPLATE ID</label>
                <input className="form-input form-input-mono" value={editingPreset.templateId}
                  onChange={e => updateField('templateId', e.target.value)} placeholder="UUID from Voicer bot" />
              </div>
            </div>

            <div className="form-field">
              <label className="form-label">DESCRIPTION</label>
              <textarea className="form-textarea" value={editingPreset.description}
                onChange={e => updateField('description', e.target.value)}
                placeholder="Describe voice character, use case..." rows={3} />
            </div>

            <div className="form-field">
              <label className="form-label">LANGUAGES</label>
              <div className="form-hint">Select languages this voice will be used for</div>
              <div className="lang-chips">
                {LANGUAGES.filter(l => l !== 'ALL').map(lang => (
                  <button key={lang}
                    className={`lang-chip ${editingPreset.languages.includes(lang) ? 'active' : ''}`}
                    onClick={() => toggleLanguage(lang)}>{lang}</button>
                ))}
              </div>
            </div>
          </div>

          <div className="presets-footer">
            <div />
            <div className="footer-buttons">
              <button className="btn-secondary" onClick={handleFormCancel}>BACK</button>
              <button className="btn-primary" onClick={handleFormSave}
                disabled={!editingPreset.name.trim() || !editingPreset.templateId.trim()}>
                <span className="material-symbols-outlined btn-icon">{isNew ? 'add' : 'check'}</span>
                {isNew ? 'ADD PRESET' : 'SAVE'}
              </button>
            </div>
          </div>
        </div>
      </div>,
      document.body
    );
  }

  // ── Render: Presets List ──────────────────────────────
  return createPortal(
    <>
      <div className="presets-overlay" onClick={handleOverlayClick}>
        <div className="presets-popup" onClick={e => e.stopPropagation()}>
          <div className="presets-header">
            <div className="presets-title-group">
              <span className="material-symbols-outlined presets-icon">playlist_add</span>
              <span className="presets-title">VOICE PRESETS</span>
              <span className="presets-badge">{localPresets.length} presets</span>
            </div>
            <div className="header-right">
              <div className="header-btn-group">
                <button className="btn-subtle" onClick={handleExport}>
                  <span className="material-symbols-outlined btn-icon">upload</span>EXPORT
                </button>
                <button className="btn-subtle" onClick={handleImport}>
                  <span className="material-symbols-outlined btn-icon">download</span>IMPORT
                </button>
              </div>
              <button className="presets-close-btn" onClick={requestClose}>
                <span className="material-symbols-outlined">close</span>
              </button>
            </div>
            <input type="file" ref={fileInputRef} accept=".json"
              style={{ display: 'none' }} onChange={handleFileChange} />
          </div>

          <div className="presets-tabs">
            {LANGUAGES.map(lang => (
              <button key={lang}
                className={`presets-tab ${activeTab === lang ? 'active' : ''}`}
                onClick={() => setActiveTab(lang)}>{lang}</button>
            ))}
          </div>

          <div className={`presets-list ${dragState ? 'is-dragging' : ''}`} ref={listRef}>
            {displayPresets.length === 0 && (
              <div className="presets-empty">
                No presets{isLangTab ? ` for ${activeTab}` : ''}. Add one below.
              </div>
            )}
            {displayPresets.map((preset, displayIndex) => {
              const isDragged = dragState && dragState.displayIndex === displayIndex;
              const langPosition = isLangTab ? (preset.langPriority?.[activeTab] ?? displayIndex) + 1 : null;
              return (
                <div
                  key={preset.id}
                  ref={el => { if (el) cardRefs.current[preset.id] = el; }}
                  className={`preset-card ${isDragged ? 'is-dragged' : ''} ${isLangTab ? 'draggable' : ''}`}
                  style={isLangTab ? getCardStyle(displayIndex) : {}}
                  onPointerDown={isLangTab ? (e) => handlePointerDown(e, displayIndex) : undefined}
                >
                  <div className="preset-card-header">
                    <div className="preset-info">
                      {isLangTab && (
                        <span className="drag-handle material-symbols-outlined">drag_indicator</span>
                      )}
                      {isLangTab && (
                        <span className="preset-position">{langPosition}</span>
                      )}
                      <div className="preset-name-block">
                        <span className="preset-name">{preset.name || 'Unnamed'}</span>
                        <span className="preset-id">ID: {preset.templateId || '—'}</span>
                      </div>
                    </div>
                    <div className="preset-actions">
                      <button className="action-btn" onClick={() => handleEdit(preset)} title="Edit">
                        <span className="material-symbols-outlined">edit</span>
                      </button>
                      <button className="action-btn action-delete" onClick={() => handleDelete(preset.id)} title="Delete">
                        <span className="material-symbols-outlined">delete</span>
                      </button>
                    </div>
                  </div>
                  {preset.description && (
                    <div className="preset-description">{preset.description}</div>
                  )}
                  {preset.languages.length > 0 && (
                    <div className="preset-tags">
                      {preset.languages.map(lang => (
                        <span key={lang} className={`preset-tag preset-tag-${lang}`}>{lang}</span>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}

            <button className="add-preset-btn" onClick={handleAdd}>
              <span className="material-symbols-outlined">add</span>
              ADD NEW PRESET
            </button>
          </div>

          <div className="presets-footer">
            <div className="footer-hint">
              <span className="material-symbols-outlined footer-hint-icon">info</span>
              <span className="footer-hint-text">Presets matched by target language</span>
            </div>
            <div className="footer-buttons">
              <button className="btn-secondary" onClick={requestClose}>CANCEL</button>
              <button className="btn-primary" onClick={handleSaveAll}>SAVE</button>
            </div>
          </div>
        </div>
      </div>
      {renderConfirmDialog()}
    </>,
    document.body
  );
};

export default PresetsPopup;
