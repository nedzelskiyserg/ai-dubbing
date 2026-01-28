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
    
    // Устанавливаем масштаб на 75% (уменьшение на 25%) для Windows
    if (process.platform === 'win32') {
      mainWindow.webContents.setZoomFactor(0.75);
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
    // Python backend может находиться в двух местах:
    // 1. resources/python-backend/ (extraResources — иногда не копируется NSIS)
    // 2. resources/app.asar.unpacked/build/python-backend/ (asarUnpack — надёжно)
    
    const resourcesPath = process.resourcesPath || path.join(path.dirname(app.getPath('exe')), 'resources');
    
    // Вариант 1: extraResources (папка рядом с app.asar)
    const backendFromExtra = path.join(resourcesPath, 'python-backend', 'api-server', 'api-server.exe');
    
    // Вариант 2: asarUnpack (распаковано из app.asar, всегда есть при сборке)
    const appAsarUnpacked = path.join(resourcesPath, 'app.asar.unpacked');
    const backendFromAsar = path.join(appAsarUnpacked, 'build', 'python-backend', 'api-server', 'api-server.exe');
    
    const candidates = [
      { path: backendFromExtra, cwd: path.join(resourcesPath, 'python-backend', 'api-server'), name: 'extraResources' },
      { path: backendFromAsar, cwd: path.join(appAsarUnpacked, 'build', 'python-backend', 'api-server'), name: 'app.asar.unpacked' },
    ];
    
    let found = null;
    for (const candidate of candidates) {
      if (fs.existsSync(candidate.path)) {
        found = candidate;
        console.log(`✅ Найден Python backend (${candidate.name}): ${candidate.path}`);
        break;
      }
      console.log(`   Проверка ${candidate.name}: ${candidate.path} — ${fs.existsSync(candidate.path) ? 'есть' : 'нет'}`);
    }
    
    if (found) {
      pythonPath = found.path;
      apiPath = '';
      cwd = found.cwd;
    } else {
      // Оба варианта не найдены — критическая ошибка сборки
      console.error('❌ КРИТИЧЕСКАЯ ОШИБКА: Упакованный Python backend не найден!');
      console.error(`   Проверено: ${backendFromExtra}`);
      console.error(`   Проверено: ${backendFromAsar}`);
      
      const { dialog } = require('electron');
      dialog.showErrorBox(
        'Критическая ошибка: Python backend не найден',
        `Упакованный Python backend не найден в приложении.\n\n` +
        `Проверены пути:\n` +
        `1. ${backendFromExtra}\n` +
        `2. ${backendFromAsar}\n\n` +
        `Это означает, что приложение было собрано некорректно.\n\n` +
        `Пожалуйста:\n` +
        `1. Переустановите приложение из официального источника\n` +
        `2. Если проблема сохраняется, сообщите разработчикам\n\n` +
        `Приложение не может работать без Python backend.`
      );
      return;
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
  
  // Логируем информацию о запуске
  console.log('🔧 Запуск API сервера:');
  console.log(`   Python: ${pythonPath}`);
  console.log(`   API Path: ${apiPath || '(исполняемый файл)'}`);
  console.log(`   CWD: ${cwd}`);
  console.log(`   Packaged: ${app.isPackaged}`);
  
  // Финальная проверка существования файлов перед запуском
  if (app.isPackaged) {
    // В упакованном приложении ДОЛЖЕН быть только .exe файл
    if (!pythonPath.endsWith('.exe')) {
      console.error(`❌ КРИТИЧЕСКАЯ ОШИБКА: В упакованном приложении должен использоваться .exe файл!`);
      const { dialog } = require('electron');
      dialog.showErrorBox(
        'Критическая ошибка конфигурации',
        `В упакованном приложении обнаружена попытка использовать системный Python.\n\n` +
        `Это недопустимо. Приложение должно использовать упакованный api-server.exe.\n\n` +
        `Приложение было собрано некорректно.`
      );
      return;
    }
    
    if (!fs.existsSync(pythonPath)) {
      console.error(`❌ Файл не найден: ${pythonPath}`);
      console.error(`   Абсолютный путь: ${path.resolve(pythonPath)}`);
      
      // Показываем детальную информацию для диагностики
      const { dialog } = require('electron');
      dialog.showErrorBox(
        'Ошибка: Python backend не найден',
        `Упакованный Python backend не найден:\n\n` +
        `Путь: ${pythonPath}\n` +
        `Абсолютный путь: ${path.resolve(pythonPath)}\n\n` +
        `Возможные причины:\n` +
        `1. Приложение было собрано некорректно\n` +
        `2. Файлы были удалены или повреждены\n` +
        `3. Антивирус удалил файл\n\n` +
        `Решение:\n` +
        `- Переустановите приложение\n` +
        `- Проверьте антивирус\n` +
        `- Скачайте свежую версию`
      );
      return;
    }
    
    console.log(`✅ Упакованный backend найден: ${pythonPath}`);
  } else {
    // В режиме разработки проверяем оба файла
    if (!fs.existsSync(apiPath)) {
      console.error(`❌ Файл не найден: ${apiPath}`);
      const { dialog } = require('electron');
      dialog.showErrorBox(
        'Ошибка запуска',
        `API сервер не найден:\n${apiPath}\n\n` +
        `Проверьте, что вы находитесь в правильной директории.`
      );
      return;
    }
    
    if (!fs.existsSync(pythonPath)) {
      console.error(`❌ Python не найден: ${pythonPath}`);
      const { dialog } = require('electron');
      dialog.showErrorBox(
        'Ошибка запуска',
        `Python не найден:\n${pythonPath}\n\n` +
        `Установите Python 3.10+ и добавьте его в PATH,\n` +
        `или создайте виртуальное окружение (.venv).`
      );
      return;
    }
  }
  
  // Если это упакованный backend (exe), запускаем напрямую
  if (app.isPackaged && pythonPath.endsWith('.exe')) {
    console.log('🚀 Запуск упакованного backend...');
    apiServer = spawn(pythonPath, [], serverOptions);
  } else {
    // Иначе запускаем Python скрипт
    console.log('🚀 Запуск Python скрипта...');
    apiServer = spawn(pythonPath, [apiPath], serverOptions);
  }
  
  if (!apiServer) {
    console.error('❌ Не удалось создать процесс API сервера');
    return;
  }
  
  console.log(`✅ Процесс API сервера создан (PID: ${apiServer.pid})`);

  // Буфер stderr для показа в диалоге при падении (пользователь не видит консоль в упакованном приложении)
  apiServer._stderrBuffer = [];
  const MAX_STDERR_LENGTH = 4000;

  apiServer.stdout.on('data', (data) => {
    const output = data.toString();
    console.log(`API: ${output}`);
    
    // Проверяем, запустился ли сервер (обычно Flask выводит "Running on")
    if (output.includes('Running on') || output.includes('Serving Flask app') || output.includes('* Running on')) {
      console.log('✅ API сервер запустился и слушает порт');
    }
  });

  apiServer.stderr.on('data', (data) => {
    const error = data.toString();
    console.error(`API Error: ${error}`);
    apiServer._stderrBuffer.push(error);
    let total = apiServer._stderrBuffer.join('').length;
    while (total > MAX_STDERR_LENGTH && apiServer._stderrBuffer.length > 1) {
      apiServer._stderrBuffer.shift();
      total = apiServer._stderrBuffer.join('').length;
    }
    
    // Не все ошибки критичны - Flask может выводить предупреждения в stderr
    if (error.includes('ERROR') || error.includes('Traceback') || error.includes('Exception') || error.includes('ModuleNotFoundError') || error.includes('ImportError')) {
      console.error('❌ Критическая ошибка API сервера:', error);
      
      // Показываем критическую ошибку пользователю
      if (mainWindow && !mainWindow.isDestroyed()) {
        const { dialog } = require('electron');
        // Показываем только первую критическую ошибку
        if (!apiServer._errorShown) {
          apiServer._errorShown = true;
          dialog.showErrorBox(
            'Критическая ошибка API сервера',
            `API сервер выдал критическую ошибку:\n\n${error.substring(0, 500)}\n\n` +
            `Проверьте логи для полной информации.`
          );
        }
      }
    }
  });

  apiServer.on('close', (code, signal) => {
    console.log(`API сервер завершился (код: ${code}, сигнал: ${signal})`);
    if (code !== 0 && code !== null) {
      console.error(`❌ API сервер завершился с ошибкой (код: ${code})`);
      
      // Показываем ошибку пользователю, если это не ожидаемое завершение
      if (code !== null && code !== 0 && !apiServer.killed) {
        const { dialog } = require('electron');
        const stderrText = (apiServer._stderrBuffer && apiServer._stderrBuffer.length)
          ? apiServer._stderrBuffer.join('').trim().slice(-MAX_STDERR_LENGTH)
          : '';
        const detailBlock = stderrText
          ? `\n\nВывод сервера (ошибка):\n${stderrText.replace(/\r\n/g, '\n')}`
          : '\n\n(Вывод сервера пуст — перезапустите приложение с открытой консолью разработчика: Ctrl+Shift+I → Console.)';
        dialog.showErrorBox(
          'API сервер завершился с ошибкой',
          `API сервер неожиданно завершился с кодом ${code}.${detailBlock}\n\n` +
          `Возможные причины:\n` +
          `1. Ошибка в Python коде или отсутствующий модуль\n` +
          `2. Отсутствуют зависимости / несовместимая версия Windows\n` +
          `3. Антивирус блокирует или удалил файлы backend\n\n` +
          `Попробуйте перезапустить приложение или переустановить.`
        );
      }
    }
  });

  apiServer.on('error', (error) => {
    console.error(`❌ Ошибка запуска API сервера: ${error.message || error}`);
    console.error(`   Код ошибки: ${error.code}`);
    console.error(`   Путь: ${pythonPath}`);
    
    // Показываем понятное сообщение пользователю
    const { dialog } = require('electron');
    let errorMessage = `Не удалось запустить Python backend.\n\n`;
    
    if (error.code === 'ENOENT') {
      errorMessage += `Ошибка: Файл не найден.\n`;
      errorMessage += `Путь: ${pythonPath}\n\n`;
      if (app.isPackaged) {
        errorMessage += `Возможные причины:\n`;
        errorMessage += `1. Python backend не был собран при упаковке\n`;
        errorMessage += `2. Файлы приложения повреждены\n\n`;
        errorMessage += `Попробуйте переустановить приложение.`;
      } else {
        errorMessage += `Убедитесь, что Python установлен и доступен в PATH.`;
      }
    } else if (error.code === 'EACCES') {
      errorMessage += `Ошибка: Нет прав доступа к файлу.\n`;
      errorMessage += `Проверьте права доступа к: ${pythonPath}`;
    } else {
      errorMessage += `Ошибка: ${error.message || error.code || 'Неизвестная ошибка'}\n\n`;
      if (app.isPackaged) {
        errorMessage += `Возможные причины:\n`;
        errorMessage += `1. Python backend не был собран при упаковке\n`;
        errorMessage += `2. Антивирус блокирует запуск\n`;
        errorMessage += `3. Недостаточно прав для запуска\n\n`;
        errorMessage += `Попробуйте:\n`;
        errorMessage += `- Переустановить приложение\n`;
        errorMessage += `- Добавить исключение в антивирус\n`;
        errorMessage += `- Запустить от имени администратора`;
      } else {
        errorMessage += `Проверьте, что Python установлен корректно.`;
      }
    }
    
    dialog.showErrorBox('Ошибка запуска API сервера', errorMessage);
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

// Ожидание готовности API сервера с повторными попытками
function waitForApiServer(maxAttempts = 30, delay = 1000) {
  return new Promise((resolve, reject) => {
    let attempts = 0;
    
    const check = async () => {
      attempts++;
      const isRunning = await checkApiServer();
      
      if (isRunning) {
        console.log('✅ API сервер готов');
        resolve(true);
      } else if (attempts >= maxAttempts) {
        console.error('❌ API сервер не запустился за отведенное время');
        reject(new Error('API server did not start in time'));
      } else {
        // Продолжаем проверку
        setTimeout(check, delay);
      }
    };
    
    check();
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
    
    // Ждем, пока API сервер станет доступен (максимум 30 секунд)
    try {
      console.log('⏳ Ожидание готовности API сервера...');
      await waitForApiServer(30, 1000);
      console.log('✅ API сервер успешно запущен и готов к работе');
    } catch (error) {
      console.error('❌ Не удалось дождаться запуска API сервера:', error);
      
      // Проверяем, запущен ли процесс
      if (apiServer && !apiServer.killed) {
        console.log(`   Процесс все еще запущен (PID: ${apiServer.pid})`);
        console.log('   Возможно, сервер еще запускается или есть проблемы с портом');
      } else {
        console.error('   Процесс API сервера не запущен или завершился');
      }
      
      // Показываем предупреждение пользователю
      const { dialog } = require('electron');
      dialog.showErrorBox(
        'Предупреждение: API сервер не отвечает',
        `API сервер не стал доступен за отведенное время.\n\n` +
        `Возможные причины:\n` +
        `1. Сервер еще запускается (подождите несколько секунд)\n` +
        `2. Порт 5001 занят другим приложением\n` +
        `3. Ошибка при запуске Python backend\n\n` +
        `Проверьте логи в консоли для деталей.\n` +
        `Попробуйте перезапустить приложение.`
      );
    }
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
