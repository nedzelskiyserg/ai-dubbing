import React, { useContext } from 'react';
import './QueuePanel.css';
import { AppContext } from '../AppContext';

const STAGES = [
  { key: 'download',   label: 'DOWNLOAD'  },
  { key: 'transcribe', label: 'TRANSCRIBE' },
  { key: 'translate',  label: 'TRANSLATE' },
  { key: 'voice',      label: 'VOICE'     },
  { key: 'render',     label: 'RENDER'    },
];

const STATUS_LABELS = {
  waiting: 'WAITING',
  processing: 'PROCESSING',
  done: '✓ DONE',
  error: 'ERROR',
  cancelled: 'CANCELLED',
};

function stageIndex(stageKey) {
  if (!stageKey) return -1;
  return STAGES.findIndex((s) => s.key === stageKey);
}

function formatDuration(seconds) {
  if (!seconds || seconds < 0) return '—';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function ActiveCard({ item, onCancel }) {
  const currentIdx = stageIndex(item.stage);
  const elapsed = item.started_at ? (Date.now() / 1000 - item.started_at) : 0;

  return (
    <div className="queue-active-card">
      <div className="qac-top">
        <div className="qac-thumb">
          <span className="material-symbols-outlined">play_circle</span>
        </div>
        <div className="qac-info">
          <div className="qac-top-row">
            <div className="qac-title-group">
              <span className="qac-position">#01</span>
              <span className="qac-title">{item.title}</span>
            </div>
            <div className="qac-status-chip">● {STATUS_LABELS[item.status] || item.status}</div>
          </div>
          <div className="qac-meta">
            {item.source_type === 'youtube' ? item.source : 'Local file'}
          </div>
        </div>
      </div>

      <div className="qac-pipeline">
        {STAGES.map((stage, i) => {
          let state = 'pending';
          if (currentIdx === -1) state = 'pending';
          else if (i < currentIdx) state = 'done';
          else if (i === currentIdx) state = 'active';
          return (
            <div key={stage.key} className={`qac-stage qac-stage--${state}`}>
              <span className="material-symbols-outlined qac-stage-icon">
                {state === 'done'
                  ? 'check_circle'
                  : state === 'active'
                  ? 'radio_button_checked'
                  : 'radio_button_unchecked'}
              </span>
              <span className="qac-stage-label">{stage.label}</span>
            </div>
          );
        })}
      </div>

      <div className="qac-progress-row">
        <div className="qac-progress-bar">
          <div
            className="qac-progress-fill"
            style={{ width: `${Math.max(0, Math.min(100, item.progress || 0))}%` }}
          />
        </div>
        <div className="qac-progress-stats">
          <span className="qac-progress-percent">{Math.round(item.progress || 0)}%</span>
          <span className="qac-progress-elapsed">{formatDuration(elapsed)}</span>
        </div>
      </div>

      <div className="qac-bottom-row">
        <span className="qac-stage-text">
          {item.stage ? `▸ ${item.stage.toUpperCase()}` : '▸ STARTING…'}
        </span>
        <button className="qac-cancel-btn" onClick={onCancel}>
          <span className="material-symbols-outlined">stop_circle</span>
          <span>CANCEL</span>
        </button>
      </div>
    </div>
  );
}

function WaitingRow({ item, position, onRemove, isNextUp }) {
  return (
    <div className="queue-row queue-row--waiting">
      <span className="qrow-position">#{position.toString().padStart(2, '0')}</span>
      <div className="qrow-thumb">
        <span className="material-symbols-outlined">movie</span>
      </div>
      <div className="qrow-mid">
        <div className="qrow-title">{item.title}</div>
        <div className="qrow-sub">
          {item.source_type === 'youtube' ? 'YouTube' : 'Local file'}
        </div>
      </div>
      <div className={`qrow-chip ${isNextUp ? 'qrow-chip--next' : ''}`}>
        {isNextUp ? 'NEXT UP' : 'WAITING'}
      </div>
      <button
        className="qrow-icon-btn"
        onClick={() => onRemove(item.id)}
        title="Remove from queue"
      >
        <span className="material-symbols-outlined">close</span>
      </button>
    </div>
  );
}

function DoneRow({ item }) {
  const took = (item.started_at && item.finished_at)
    ? item.finished_at - item.started_at
    : null;
  const statusText = item.status === 'done'
    ? `done in ${formatDuration(took)}`
    : item.status === 'error'
    ? `failed: ${(item.error || '').slice(0, 60)}`
    : 'cancelled';

  return (
    <div className={`queue-row queue-row--done queue-row--${item.status}`}>
      <span className="material-symbols-outlined qrow-done-icon">
        {item.status === 'done' ? 'check_circle' : item.status === 'error' ? 'error' : 'cancel'}
      </span>
      <div className="qrow-thumb">
        <span className="material-symbols-outlined">movie</span>
      </div>
      <div className="qrow-mid">
        <div className="qrow-title">{item.title}</div>
        <div className="qrow-sub">{statusText}</div>
      </div>
      <div className="qrow-chip">{STATUS_LABELS[item.status] || item.status}</div>
    </div>
  );
}

const QueuePanel = () => {
  const { queue, removeFromQueue, cancelCurrentItem, clearDoneItems } = useContext(AppContext);

  const items = queue?.items || [];
  if (items.length === 0) {
    return null; // Пустая очередь — не показываем панель.
  }

  const active = items.find((i) => i.status === 'processing');
  const waiting = items.filter((i) => i.status === 'waiting');
  const finished = items.filter((i) => ['done', 'error', 'cancelled'].includes(i.status));
  const totalActive = queue.counts?.waiting + (active ? 1 : 0);
  const activeGlobalIdx = items.findIndex((i) => i.id === active?.id);

  return (
    <div className="panel queue-panel">
      <div className="panel-header queue-panel-header">
        <div className="queue-header-left">
          <span className="panel-label">[QUEUE:VIDEOS]</span>
          <span className="panel-title">PROCESSING QUEUE</span>
          <span className="material-symbols-outlined panel-icon">playlist_play</span>
        </div>
        <div className="queue-header-right">
          <div className="queue-badge">
            {totalActive} {totalActive === 1 ? 'IN QUEUE' : 'IN QUEUE'}
          </div>
          {finished.length > 0 && (
            <button className="queue-clear-btn" onClick={clearDoneItems}>
              <span className="material-symbols-outlined">clear_all</span>
              <span>CLEAR DONE</span>
            </button>
          )}
        </div>
      </div>
      <div className="panel-content queue-panel-content">
        {active && (
          <ActiveCard item={active} onCancel={cancelCurrentItem} />
        )}
        {waiting.map((item, i) => {
          // Абсолютная позиция = (индекс активного + 1) + i, или (i+1) если нет активного
          const position = (activeGlobalIdx >= 0 ? activeGlobalIdx + 2 : 1) + i;
          return (
            <WaitingRow
              key={item.id}
              item={item}
              position={position}
              onRemove={removeFromQueue}
              isNextUp={i === 0 && !!active}
            />
          );
        })}
        {finished.map((item) => (
          <DoneRow key={item.id} item={item} />
        ))}
      </div>
    </div>
  );
};

export default QueuePanel;
