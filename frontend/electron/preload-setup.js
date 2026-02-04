/**
 * Preload-скрипт для окна установки зависимостей.
 * Экспонирует IPC-каналы для получения прогресса из main process.
 */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('setupAPI', {
  /** Подписка на обновление прогресса: (step, percent, message) */
  onProgress: (callback) => {
    ipcRenderer.on('setup-progress', (_event, step, percent, message) => {
      callback(step, percent, message);
    });
  },
  /** Подписка на завершение установки */
  onComplete: (callback) => {
    ipcRenderer.on('setup-complete', () => callback());
  },
  /** Подписка на ошибку установки: (errorMessage) */
  onError: (callback) => {
    ipcRenderer.on('setup-error', (_event, errorMessage) => callback(errorMessage));
  },
  /** Отмена установки (закрытие приложения) */
  cancel: () => {
    ipcRenderer.send('setup-cancel');
  },
});
