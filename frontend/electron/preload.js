// Preload скрипт для безопасной связи между Electron и веб-контентом
const { contextBridge, ipcRenderer } = require('electron');

// Экспортируем безопасные API для использования в React
contextBridge.exposeInMainWorld('electronAPI', {
  platform: process.platform,
  versions: {
    node: process.versions.node,
    chrome: process.versions.chrome,
    electron: process.versions.electron
  },
  // API для работы с буфером обмена
  clipboard: {
    readText: () => ipcRenderer.invoke('clipboard-read-text'),
    writeText: (text) => ipcRenderer.invoke('clipboard-write-text', text)
  },
  // API для обновления Electron-оболочки
  update: {
    onUpdateAvailable: (cb) => ipcRenderer.on('electron-update-available', (_e, version) => cb(version)),
    onUpdateDownloaded: (cb) => ipcRenderer.on('electron-update-downloaded', (_e, version) => cb(version)),
    downloadUpdate: () => ipcRenderer.invoke('electron-update-download'),
    installUpdate: () => ipcRenderer.invoke('electron-update-install'),
  }
});
