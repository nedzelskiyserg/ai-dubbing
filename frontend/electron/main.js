const { app, BrowserWindow, Menu, clipboard, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

let mainWindow;
let apiServer;

// Функция для создания окна
function createWindow() {
  // Получаем размеры экрана
  const { screen } = require('electron');
  const primaryDisplay = screen.getPrimaryDisplay();
  const { width, height } = primaryDisplay.workAreaSize;

  mainWindow = new BrowserWindow({
    width: Math.min(1920, width),
    height: Math.min(1080, height),
    minWidth: 1200,
    minHeight: 700,
    backgroundColor: '#1A1A1A',
    fullscreen: false, // Явно отключаем полноэкранный режим
    fullscreenable: true, // Разрешаем переключение через F11
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      enableRemoteModule: false,
      webSecurity: true,
      preload: path.join(__dirname, 'preload.js'),
      spellcheck: false
    },
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    frame: true,
    show: false,
    autoHideMenuBar: false,
    // Добавляем отступ сверху для macOS (traffic lights)
    titleBarOverlay: process.platform === 'darwin' ? {
      color: '#1A1A1A',
      symbolColor: '#FFFFFF',
      height: 40
    } : undefined,
    icon: process.platform === 'win32' ? path.join(__dirname, '../build/icon.ico') : undefined
  });

  // Загружаем React приложение
  const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged;
  
  if (isDev) {
    // В режиме разработки подключаемся к React dev server
    mainWindow.loadURL('http://localhost:3000');
    
    // Открываем DevTools в режиме разработки (опционально)
    // mainWindow.webContents.openDevTools();
  } else {
    // В production загружаем из build
    mainWindow.loadFile(path.join(__dirname, '../build/index.html'));
  }

  // Показываем окно когда готово
  mainWindow.once('ready-to-show', () => {
    // Убеждаемся, что окно не в полноэкранном режиме
    if (mainWindow.isFullScreen()) {
      mainWindow.setFullScreen(false);
    }
    mainWindow.show();
    mainWindow.center(); // Центрируем окно
    mainWindow.focus(); // Фокусируемся на окне
  });

  // Обработка закрытия окна - принудительно останавливаем все процессы
  mainWindow.on('close', async (event) => {
    // НЕ предотвращаем закрытие - всегда разрешаем
    // Отправляем запрос на остановку процесса через API
    try {
      const http = require('http');
      const postData = JSON.stringify({});
      
      const options = {
        hostname: 'localhost',
        port: 5001,
        path: '/api/stop',
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(postData)
        },
        timeout: 1000
      };
      
      const req = http.request(options, (res) => {
        console.log('Запрос на остановку отправлен');
      });
      
      req.on('error', (err) => {
        console.log('Ошибка отправки запроса на остановку:', err.message);
      });
      
      req.on('timeout', () => {
        req.destroy();
      });
      
      req.write(postData);
      req.end();
      
      // Даем немного времени на обработку запроса (но не блокируем закрытие)
      setTimeout(() => {
        // Принудительно завершаем все процессы
        if (apiServer) {
          console.log('Принудительное завершение API сервера...');
          apiServer.kill('SIGTERM');
          // Если не завершился за 2 секунды, убиваем принудительно
          setTimeout(() => {
            if (apiServer && !apiServer.killed) {
              console.log('Принудительное убийство API сервера...');
              apiServer.kill('SIGKILL');
            }
          }, 2000);
        }
      }, 100);
    } catch (error) {
      console.log('Ошибка при остановке процесса:', error);
      // Все равно завершаем процессы
      if (apiServer) {
        apiServer.kill('SIGTERM');
        setTimeout(() => {
          if (apiServer && !apiServer.killed) {
            apiServer.kill('SIGKILL');
          }
        }, 2000);
      }
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  // Обработка ошибок загрузки
  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    if (errorCode === -106) {
      // ERR_INTERNET_DISCONNECTED - React еще не запустился
      console.log('Ожидание запуска React приложения...');
      setTimeout(() => {
        mainWindow.reload();
      }, 2000);
    }
  });

  // Применяем контекстное меню
  mainWindow.webContents.on('context-menu', (e, params) => {
    // Создаем контекстное меню с опцией Inspect Element
    const contextMenu = Menu.buildFromTemplate([
      { role: 'cut', label: 'Cut', accelerator: 'CmdOrCtrl+X' },
      { role: 'copy', label: 'Copy', accelerator: 'CmdOrCtrl+C' },
      { role: 'paste', label: 'Paste', accelerator: 'CmdOrCtrl+V' },
      { role: 'selectAll', label: 'Select All', accelerator: 'CmdOrCtrl+A' },
      { type: 'separator' },
      { role: 'undo', label: 'Undo', accelerator: 'CmdOrCtrl+Z' },
      { role: 'redo', label: 'Redo', accelerator: process.platform === 'darwin' ? 'Cmd+Shift+Z' : 'Ctrl+Y' },
      { type: 'separator' },
      {
        label: 'Inspect Element',
        click: () => {
          // Открываем DevTools если закрыты
          if (!mainWindow.webContents.isDevToolsOpened()) {
            mainWindow.webContents.openDevTools();
          }
          // Выделяем элемент под курсором
          mainWindow.webContents.inspectElement(params.x, params.y);
        }
      }
    ]);
    
    contextMenu.popup();
  });

  // Включаем стандартные действия клавиатуры
  mainWindow.webContents.on('before-input-event', (event, input) => {
    // Разрешаем все стандартные комбинации клавиш
    const { control, meta, shift, key } = input;
    const cmdOrCtrl = control || meta;
    
    // Разрешаем стандартные действия (копирование, вставка и т.д.)
    if (cmdOrCtrl && (key === 'c' || key === 'v' || key === 'x' || key === 'a' || key === 'z')) {
      // Разрешаем стандартное поведение браузера
      return;
    }
  });

  // Создаем меню
  createMenu();
}

// Создание меню приложения
function createMenu() {
  const template = [
    {
      label: 'File',
      submenu: [
        {
          label: 'Exit',
          accelerator: process.platform === 'darwin' ? 'Cmd+Q' : 'Ctrl+Q',
          click: () => {
            app.quit();
          }
        }
      ]
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo', label: 'Undo', accelerator: 'CmdOrCtrl+Z' },
        { role: 'redo', label: 'Redo', accelerator: process.platform === 'darwin' ? 'Cmd+Shift+Z' : 'Ctrl+Y' },
        { type: 'separator' },
        { role: 'cut', label: 'Cut', accelerator: 'CmdOrCtrl+X' },
        { role: 'copy', label: 'Copy', accelerator: 'CmdOrCtrl+C' },
        { role: 'paste', label: 'Paste', accelerator: 'CmdOrCtrl+V' },
        { role: 'selectAll', label: 'Select All', accelerator: 'CmdOrCtrl+A' }
      ]
    },
    {
      label: 'View',
      submenu: [
        {
          label: 'Reload',
          accelerator: 'CmdOrCtrl+R',
          click: (item, focusedWindow) => {
            if (focusedWindow) focusedWindow.reload();
          }
        },
        {
          label: 'Toggle Full Screen',
          accelerator: process.platform === 'darwin' ? 'Ctrl+Cmd+F' : 'F11',
          click: (item, focusedWindow) => {
            if (focusedWindow) {
              focusedWindow.setFullScreen(!focusedWindow.isFullScreen());
            }
          }
        },
        {
          type: 'separator'
        },
        {
          label: 'Toggle Developer Tools',
          accelerator: process.platform === 'darwin' ? 'Alt+Cmd+I' : 'Ctrl+Shift+I',
          click: (item, focusedWindow) => {
            if (focusedWindow) {
              focusedWindow.webContents.toggleDevTools();
            }
          }
        }
      ]
    },
    {
      label: 'Window',
      submenu: [
        {
          label: 'Minimize',
          accelerator: 'CmdOrCtrl+M',
          role: 'minimize'
        },
        {
          label: 'Close',
          accelerator: 'CmdOrCtrl+W',
          role: 'close'
        }
      ]
    }
  ];

  // macOS специфичное меню
  if (process.platform === 'darwin') {
    template.unshift({
      label: app.getName(),
      submenu: [
        {
          label: 'About ' + app.getName(),
          role: 'about'
        },
        {
          type: 'separator'
        },
        {
          label: 'Services',
          role: 'services',
          submenu: []
        },
        {
          type: 'separator'
        },
        {
          label: 'Hide ' + app.getName(),
          accelerator: 'Command+H',
          role: 'hide'
        },
        {
          label: 'Hide Others',
          accelerator: 'Command+Shift+H',
          role: 'hideothers'
        },
        {
          label: 'Show All',
          role: 'unhide'
        },
        {
          type: 'separator'
        },
        {
          label: 'Quit',
          accelerator: 'Command+Q',
          click: () => {
            app.quit();
          }
        }
      ]
    });
  }

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}

// Запуск API сервера
function startApiServer() {
  const fs = require('fs');
  
  // Определяем пути в зависимости от режима (dev/production)
  let apiPath, pythonPath, cwd;
  
  if (app.isPackaged) {
    // В production режиме (упакованное приложение)
    // Пути относительно ресурсов Electron
    const resourcesPath = process.resourcesPath || path.join(__dirname, '..');
    
    // Проверяем наличие упакованного Python backend
    const packagedBackend = path.join(resourcesPath, 'python-backend', 'api-server', 'api-server.exe');
    
    if (fs.existsSync(packagedBackend)) {
      // Используем упакованный Python backend
      pythonPath = packagedBackend;
      apiPath = ''; // Не нужен, так как это уже исполняемый файл
      cwd = path.join(resourcesPath, 'python-backend', 'api-server');
    } else {
      // Fallback: используем исходный Python скрипт
      apiPath = path.join(resourcesPath, 'src', 'api_server.py');
      cwd = resourcesPath;
      pythonPath = process.env.PYTHON_PATH || (process.platform === 'win32' ? 'python' : 'python3');
    }
  } else {
    // В режиме разработки
    apiPath = path.join(__dirname, '../../src/api_server.py');
    cwd = path.join(__dirname, '../..');
    
    // Проверяем наличие venv
    const venvPython = process.platform === 'win32' 
      ? path.join(__dirname, '../../.venv/Scripts/python.exe')
      : path.join(__dirname, '../../.venv/bin/python3');
    
    pythonPath = process.env.PYTHON_PATH || (fs.existsSync(venvPython) ? venvPython : (process.platform === 'win32' ? 'python' : 'python3'));
  }
  
  const serverOptions = {
    cwd: cwd,
    stdio: 'pipe',
  };

  // На Windows нужно использовать shell
  if (process.platform === 'win32') {
    serverOptions.shell = true;
  }
  
  // Если это упакованный backend (exe), запускаем напрямую
  if (app.isPackaged && pythonPath.endsWith('.exe')) {
    apiServer = spawn(pythonPath, [], serverOptions);
  } else {
    // Иначе запускаем Python скрипт
    apiServer = spawn(pythonPath, [apiPath], serverOptions);
  }

  apiServer.stdout.on('data', (data) => {
    console.log(`API: ${data}`);
  });

  apiServer.stderr.on('data', (data) => {
    console.error(`API Error: ${data}`);
  });

  apiServer.on('close', (code) => {
    console.log(`API сервер завершился с кодом ${code}`);
  });

  apiServer.on('error', (error) => {
    console.error(`Ошибка запуска API сервера: ${error}`);
    
    // Показываем понятное сообщение пользователю
    if (app.isPackaged) {
      const { dialog } = require('electron');
      dialog.showErrorBox(
        'Ошибка запуска',
        `Не удалось запустить Python backend.\n\n` +
        `Возможные причины:\n` +
        `1. Python не установлен на системе\n` +
        `2. Python backend не был собран при упаковке\n\n` +
        `Пожалуйста, установите Python 3.10+ и убедитесь, что все зависимости установлены.`
      );
    }
  });
}

// Проверка, запущен ли API сервер
function checkApiServer() {
  return new Promise((resolve) => {
    const http = require('http');
    const req = http.get('http://localhost:5001/api/health', (res) => {
      resolve(true);
    });
    req.on('error', () => {
      resolve(false);
    });
    req.setTimeout(1000, () => {
      req.destroy();
      resolve(false);
    });
  });
}

// Обработчики IPC для работы с буфером обмена
ipcMain.handle('clipboard-read-text', () => {
  return clipboard.readText();
});

ipcMain.handle('clipboard-write-text', (event, text) => {
  clipboard.writeText(text);
  return true;
});

// Когда Electron готов
app.whenReady().then(async () => {
  // Проверяем, запущен ли API сервер (если запущен из скрипта, не запускаем еще раз)
  const apiRunning = await checkApiServer();
  
  if (!apiRunning) {
    // Запускаем API сервер только если он не запущен
    console.log('Запуск API сервера из Electron...');
    startApiServer();
  } else {
    console.log('API сервер уже запущен, пропускаем запуск из Electron');
  }
  
  // Создаем окно
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

// Выход когда все окна закрыты
app.on('window-all-closed', () => {
  // Принудительно останавливаем все процессы
  if (apiServer) {
    console.log('Принудительное завершение API сервера при закрытии всех окон...');
    apiServer.kill('SIGTERM');
    setTimeout(() => {
      if (apiServer && !apiServer.killed) {
        apiServer.kill('SIGKILL');
      }
    }, 2000);
  }
  
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', async (event) => {
  // Отправляем запрос на остановку процесса
  try {
    const http = require('http');
    const postData = JSON.stringify({});
    
    const options = {
      hostname: 'localhost',
      port: 5001,
      path: '/api/stop',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(postData)
      },
      timeout: 1000
    };
    
    const req = http.request(options);
    req.on('error', () => {});
    req.on('timeout', () => req.destroy());
    req.write(postData);
    req.end();
    
    await new Promise(resolve => setTimeout(resolve, 500));
  } catch (error) {
    console.log('Ошибка при остановке процесса:', error);
  }
  
  // Принудительно останавливаем API сервер
  if (apiServer) {
    console.log('Принудительное завершение API сервера перед выходом...');
    apiServer.kill('SIGTERM');
    setTimeout(() => {
      if (apiServer && !apiServer.killed) {
        apiServer.kill('SIGKILL');
      }
    }, 2000);
  }
});

// Обработка ошибок
process.on('uncaughtException', (error) => {
  console.error('Uncaught Exception:', error);
});
