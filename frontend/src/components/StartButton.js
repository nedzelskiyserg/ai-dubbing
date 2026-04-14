import React, { useContext } from 'react';
import './StartButton.css';
import { AppContext } from '../AppContext';

/**
 * StartButton — теперь управляет очередью.
 *
 * Три состояния:
 *  1. Очередь пустая → кнопка отключена («QUEUE EMPTY»).
 *  2. Есть WAITING + очередь не запущена → «▶ START QUEUE».
 *  3. Очередь запущена или выполняется элемент → «⏸ PAUSE QUEUE».
 *
 * Реальный прогресс по отдельному ролику показывается в QueuePanel — здесь
 * только управляющая кнопка.
 */
const StartButton = () => {
  const { queue, startQueue, pauseQueue } = useContext(AppContext);

  const items = queue?.items || [];
  const waitingCount = items.filter((i) => i.status === 'waiting').length;
  const hasActive = items.some((i) => i.status === 'processing');
  const isRunning = queue?.running || hasActive;
  const isPaused = queue?.paused;
  const isEmpty = waitingCount === 0 && !hasActive;

  let label, icon, action, disabled;
  if (isEmpty) {
    label = 'QUEUE EMPTY';
    icon = 'playlist_remove';
    action = null;
    disabled = true;
  } else if (isRunning && !isPaused) {
    label = 'PAUSE QUEUE';
    icon = 'pause';
    action = pauseQueue;
    disabled = false;
  } else {
    label = 'START QUEUE';
    icon = 'play_arrow';
    action = startQueue;
    disabled = false;
  }

  const handleClick = async () => {
    if (!action || disabled) return;
    try {
      await action();
    } catch (e) {
      console.error('Queue control failed:', e);
      alert(`Queue control failed: ${e.message || e}`);
    }
  };

  return (
    <button
      className={`start-button ${isRunning && !isPaused ? 'processing' : ''} ${disabled ? 'disabled' : ''}`}
      onClick={handleClick}
      disabled={disabled}
    >
      <div className="start-button-content">
        <span className="material-symbols-outlined start-icon">{icon}</span>
        <span className="start-text">{label}</span>
      </div>
    </button>
  );
};

export default StartButton;
