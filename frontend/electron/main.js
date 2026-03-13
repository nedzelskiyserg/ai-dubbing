const { app, BrowserWindow, Menu, clipboard, ipcMain, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { spawn } = require('child_process');
const depManager = require('./dependency-manager');
const autoUpdater = require('./auto-updater');

let mainWindow;
let setupWindow;
let apiServer;
let appStarting = true; // Флаг: приложение ещё запускается (не закрывать при window-all-closed)
/** Порт API (5001 или следующий свободный). Заполняется после чтения API_PORT_FILE. */
let apiPort = 5001;
let apiPortFilePath = null;

// --- Подробное диагностическое логирование (консоль + файл для .exe) ---
// Windows: %LOCALAPPDATA%\AI Dubbing Studio\electron-api-debug.log (не Документы)
// macOS/Linux: userData/electron-api-debug.log
let diagnosticLogPath = null;
function getDiagnosticLogPath() {
  if (diagnosticLogPath) return diagnosticLogPath;
  try {
    if (process.platform === 'win32') {
      const localDir = path.join(process.env.LOCALAPPDATA || path.join(process.env.USERPROFILE || '', 'AppData', 'Local'), 'AI Dubbing Studio');
      if (!fs.existsSync(localDir)) fs.mkdirSync(localDir, { recursive: true });
      diagnosticLogPath = path.join(localDir, 'electron-api-debug.log');
    } else {
      const dir = app.getPath('userData');
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
      diagnosticLogPath = path.join(dir, 'electron-api-debug.log');
    }
    return diagnosticLogPath;
  } catch (e) {
    return path.join(app.getPath('temp'), 'ai-dubbing-electron-debug.log');
  }
}
let diagnosticLogStarted = false;
function logDiag(msg, obj) {
  const line = typeof msg === 'string' ? msg : JSON.stringify(msg);
  const full = `[${new Date().toISOString()}] ${line}${obj !== undefined ? ' ' + JSON.stringify(obj) : ''}`;
  console.log(full);
  try {
    if (app.isPackaged) {
      const logPath = getDiagnosticLogPath();
      if (!diagnosticLogStarted) {
        diagnosticLogStarted = true;
        fs.appendFileSync(logPath, `\n========== AI Dubbing Studio Electron API debug ${new Date().toISOString()} ==========\nЛог: ${logPath}\n`, 'utf8');
      }
      fs.appendFileSync(logPath, full + '\n', 'utf8');
    }
  } catch (e) {}
}

/** Читает .env файл и возвращает объект с переменными. Не падает если файла нет. */
function loadDotEnv() {
  const candidates = [];
  if (app.isPackaged) {
    const resPath = process.resourcesPath || path.join(path.dirname(app.getPath('exe')), 'resources');
    candidates.push(path.join(resPath, '.env'));
  }
  candidates.push(path.join(__dirname, '..', '..', '.env'));
  candidates.push(path.join(__dirname, '..', '.env'));

  for (const envPath of candidates) {
    try {
      if (fs.existsSync(envPath)) {
        const content = fs.readFileSync(envPath, 'utf8');
        const vars = {};
        for (const line of content.split(/\r?\n/)) {
          const trimmed = line.trim();
          if (!trimmed || trimmed.startsWith('#')) continue;
          const eq = trimmed.indexOf('=');
          if (eq > 0) {
            const key = trimmed.substring(0, eq).trim();
            const val = trimmed.substring(eq + 1).trim();
            vars[key] = val;
          }
        }
        logDiag('Загружены переменные из .env', { path: envPath, keys: Object.keys(vars) });
        return vars;
      }
    } catch (e) {
      logDiag('Ошибка чтения .env', { path: envPath, error: e.message });
    }
  }
  return {};
}

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

  // Загружаем React приложение (передаём порт API в query для совпадения с backend)
  const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged;
  if (isDev) {
    const apiPortParam = apiPort !== 5001 ? `apiPort=${apiPort}` : '';
    const devUrl = apiPortParam ? `http://localhost:3000?${apiPortParam}` : 'http://localhost:3000';
    mainWindow.loadURL(devUrl);
  } else {
    // В production всегда передаём apiPort в query — фронт читает 127.0.0.1:${port}/api
    const loadOpts = { query: { apiPort: String(apiPort) } };
    mainWindow.loadFile(path.join(__dirname, '../build/index.html'), loadOpts);
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
        hostname: '127.0.0.1',
        port: apiPort,
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
  // Определяем пути в зависимости от режима (dev/production)
  let apiPath, pythonPath, cwd;

  if (app.isPackaged) {
    // В production режиме: используем скачанный Python из %LOCALAPPDATA% + исходники из resources
    pythonPath = depManager.getPythonExe();
    const srcPath = depManager.getSrcPath();
    if (!srcPath) {
      console.error('❌ КРИТИЧЕСКАЯ ОШИБКА: Исходный код Python (src/) не найден!');
      const { dialog } = require('electron');
      dialog.showErrorBox(
        'Критическая ошибка: src/ не найден',
        'Исходный код Python backend не найден в ресурсах приложения.\n\n' +
        'Переустановите приложение.'
      );
      return;
    }
    apiPath = path.join(srcPath, 'api_server.py');
    cwd = srcPath;
  } else {
    // В режиме разработки
    apiPath = path.join(__dirname, '../../src/api_server.py');
    cwd = path.join(__dirname, '../..');

    // Проверяем наличие venv (приоритет: venv_tts > .venv_f5tts > .venv > system)
    const venvCandidates = ['venv_tts', '.venv_f5tts', '.venv'];
    let venvPython = null;
    for (const venvName of venvCandidates) {
      const candidate = process.platform === 'win32'
        ? path.join(__dirname, `../../${venvName}/Scripts/python.exe`)
        : path.join(__dirname, `../../${venvName}/bin/python3`);
      if (fs.existsSync(candidate)) {
        venvPython = candidate;
        break;
      }
    }

    pythonPath = process.env.PYTHON_PATH || (venvPython || (process.platform === 'win32' ? 'python' : 'python3'));
  }

  // Уникальный файл на каждый запуск — иначе читаем порт от предыдущего процесса
  apiPortFilePath = path.join(os.tmpdir(), `ai-dubbing-api-port-${Date.now()}-${process.pid}.txt`);

  // Путь к FFmpeg — передаём через env
  const ffmpegPath = app.isPackaged ? depManager.getFFmpegExe() : (process.env.FFMPEG_PATH || '');

  // Загружаем переменные из .env файла (HF_TOKEN и др.)
  const dotEnvVars = loadDotEnv();

  // SSL-сертификаты для embedded Python (certifi CA bundle)
  const certifiPath = path.join(depManager.getPythonDir(), 'Lib', 'site-packages', 'certifi', 'cacert.pem');
  const sslEnv = fs.existsSync(certifiPath)
    ? { SSL_CERT_FILE: certifiPath, REQUESTS_CA_BUNDLE: certifiPath, CURL_CA_BUNDLE: certifiPath }
    : {};

  const serverOptions = {
    cwd: cwd,
    stdio: 'pipe',
    env: Object.assign({}, process.env, dotEnvVars, sslEnv, {
      API_PORT_FILE: apiPortFilePath,
      FFMPEG_PATH: ffmpegPath,
      PYTHONUTF8: '1',
    }),
  };

  logDiag('--- Запуск API сервера ---');
  logDiag('executable', { pythonPath, apiPath, cwd });
  logDiag('API_PORT_FILE (передаётся в env backend)', { apiPortFilePath });
  logDiag('FFMPEG_PATH', { ffmpegPath });
  logDiag('platform', { platform: process.platform, packaged: app.isPackaged });
  console.log('🔧 Запуск API сервера:');
  console.log(`   Python: ${pythonPath}`);
  console.log(`   API Path: ${apiPath}`);
  console.log(`   CWD: ${cwd}`);
  console.log(`   FFMPEG_PATH: ${ffmpegPath}`);
  console.log(`   Packaged: ${app.isPackaged}`);

  // Финальная проверка существования файлов перед запуском
  if (!fs.existsSync(pythonPath)) {
    console.error(`❌ Python не найден: ${pythonPath}`);
    const { dialog } = require('electron');
    dialog.showErrorBox(
      'Ошибка: Python не найден',
      `Python не найден:\n${pythonPath}\n\n` +
      (app.isPackaged
        ? 'Зависимости не установлены. Удалите папку "AI Dubbing Studio" из %LOCALAPPDATA% и перезапустите приложение.'
        : 'Установите Python 3.10+ и добавьте его в PATH,\nили создайте виртуальное окружение (.venv).')
    );
    return;
  }

  if (!fs.existsSync(apiPath)) {
    console.error(`❌ Файл не найден: ${apiPath}`);
    const { dialog } = require('electron');
    dialog.showErrorBox(
      'Ошибка запуска',
      `API сервер не найден:\n${apiPath}\n\nПереустановите приложение.`
    );
    return;
  }

  logDiag('spawn: Python скрипт', { cmd: pythonPath, args: [apiPath] });
  apiServer = spawn(pythonPath, [apiPath], serverOptions);

  if (!apiServer) {
    logDiag('ERROR: spawn вернул null');
    return;
  }
  logDiag('процесс создан', { pid: apiServer.pid });
  console.log(`✅ Процесс API сервера создан (PID: ${apiServer.pid})`);

  // Буфер stderr для показа в диалоге при падении (пользователь не видит консоль в упакованном приложении)
  apiServer._stderrBuffer = [];
  const MAX_STDERR_LENGTH = 4000;

  apiServer.stdout.on('data', (data) => {
    const output = data.toString();
    logDiag('API stdout', { line: output.trim().slice(0, 200) });
    console.log(`API: ${output}`);
    if (output.includes('Running on') || output.includes('Serving Flask app') || output.includes('* Running on')) {
      console.log('✅ API сервер запустился и слушает порт');
    }
  });

  apiServer.stderr.on('data', (data) => {
    const error = data.toString();
    logDiag('API stderr', { line: error.trim().slice(0, 300) });
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
    const stderrFull = (apiServer._stderrBuffer && apiServer._stderrBuffer.length)
      ? apiServer._stderrBuffer.join('').trim()
      : '';
    logDiag('API процесс завершён', { code, signal, killed: apiServer.killed, stderrLength: stderrFull.length });
    if (stderrFull) logDiag('API stderr (полный)', { text: stderrFull.slice(-2000) });
    console.log(`API сервер завершился (код: ${code}, сигнал: ${signal})`);
    if (code !== 0 && code !== null) {
      console.error(`❌ API сервер завершился с ошибкой (код: ${code})`);
      if (code !== null && code !== 0 && !apiServer.killed) {
        const { dialog } = require('electron');
        const stderrText = stderrFull.slice(-MAX_STDERR_LENGTH);
        const logPath = app.isPackaged ? getDiagnosticLogPath() : '';
        const detailBlock = stderrText
          ? `\n\nВывод сервера (ошибка):\n${stderrText.replace(/\r\n/g, '\n')}`
          : '\n\n(Вывод сервера пуст.)';
        const logHint = logPath ? `\n\nПолный лог: ${logPath}` : '';
        dialog.showErrorBox(
          'API сервер завершился с ошибкой',
          `API сервер неожиданно завершился с кодом ${code}.${detailBlock}\n\n` +
          `Возможные причины:\n` +
          `1. Ошибка в Python коде или отсутствующий модуль\n` +
          `2. Отсутствуют зависимости / несовместимая версия Windows\n` +
          `3. Антивирус блокирует или удалил файлы backend\n\n` +
          `Попробуйте перезапустить приложение или переустановить.${logHint}`
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

// Проверка, запущен ли API сервер. Возвращает { ok, statusCode, errorMessage } для логирования.
function checkApiServer(port) {
  const p = port != null ? port : apiPort;
  return new Promise((resolve) => {
    const http = require('http');
    const url = `http://127.0.0.1:${p}/api/health`;
    const req = http.get(url, (res) => {
      resolve({ ok: res.statusCode === 200, statusCode: res.statusCode, errorMessage: null });
    });
    req.on('error', (err) => {
      resolve({ ok: false, statusCode: null, errorMessage: err.code || err.message || String(err) });
    });
    req.setTimeout(2000, () => {
      req.destroy();
      resolve({ ok: false, statusCode: null, errorMessage: 'timeout' });
    });
  });
}

// Ждём появления файла с портом от backend (макс. 60 с — импорты torch/whisperx в exe занимают 15–30 с)
function waitForPortFile(filePath, maxWaitMs = 60000) {
  logDiag('waitForPortFile: начало', { filePath, maxWaitMs });
  return new Promise((resolve) => {
    const start = Date.now();
    let lastLog = 0;
    const tick = () => {
      const elapsed = Date.now() - start;
      try {
        if (fs.existsSync(filePath)) {
          const content = fs.readFileSync(filePath, 'utf8').trim();
          if (content) {
            const p = parseInt(content, 10);
            if (Number.isInteger(p) && p > 0) {
              logDiag('waitForPortFile: прочитан порт', { filePath, content, port: p, elapsed });
              resolve(p);
              return;
            }
            logDiag('waitForPortFile: файл есть, порт невалидный', { content });
          }
        } else if (elapsed - lastLog >= 1000) {
          lastLog = elapsed;
          logDiag('waitForPortFile: ждём файл', { elapsed, filePath });
        }
      } catch (e) {
        logDiag('waitForPortFile: ошибка чтения', { err: e.message, filePath });
      }
      if (elapsed >= maxWaitMs) {
        logDiag('waitForPortFile: таймаут, fallback 5001', { elapsed, filePath });
        resolve(5001);
        return;
      }
      setTimeout(tick, 100);
    };
    tick();
  });
}

// Ожидание готовности API сервера с повторными попытками (порт берётся из глобального apiPort)
function waitForApiServer(maxAttempts = 30, delay = 1000) {
  logDiag('waitForApiServer: начало', { maxAttempts, delay, apiPort });
  return new Promise((resolve, reject) => {
    let attempts = 0;
    const check = async () => {
      attempts++;
      const result = await checkApiServer();
      if (attempts <= 3 || attempts % 5 === 0 || result.ok) {
        logDiag('waitForApiServer: попытка', { attempt: attempts, port: apiPort, ok: result.ok, statusCode: result.statusCode, error: result.errorMessage });
      }
      if (result.ok) {
        logDiag('waitForApiServer: успех', { attempt: attempts, apiPort });
        console.log(`✅ API сервер готов (порт ${apiPort})`);
        resolve(true);
      } else if (attempts >= maxAttempts) {
        logDiag('waitForApiServer: таймаут', { attempts, apiPort, lastError: result.errorMessage, lastStatus: result.statusCode });
        console.error('❌ API сервер не запустился за отведенное время');
        reject(new Error('API server did not start in time'));
      } else {
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

// IPC для обновления Electron-оболочки (пользователь нажимает "Обновить")
ipcMain.handle('electron-update-download', () => {
  try {
    const { autoUpdater: electronUpdater } = require('electron-updater');
    return electronUpdater.downloadUpdate();
  } catch (e) { return null; }
});
ipcMain.handle('electron-update-install', () => {
  try {
    const { autoUpdater: electronUpdater } = require('electron-updater');
    electronUpdater.quitAndInstall();
  } catch (e) { /* ignore */ }
});

// --- Окно установки зависимостей ---

/** Показывает окно установки и запускает процесс. Возвращает Promise<void> по завершении. */
function runSetupWindow() {
  return new Promise((resolve, reject) => {
    setupWindow = new BrowserWindow({
      width: 580,
      height: 420,
      resizable: false,
      frame: false,
      backgroundColor: '#1A1A1A',
      webPreferences: {
        nodeIntegration: false,
        contextIsolation: true,
        preload: path.join(__dirname, 'preload-setup.js'),
      },
      icon: process.platform === 'win32' ? path.join(__dirname, '../build/icon.ico') : undefined,
    });

    setupWindow.loadFile(path.join(__dirname, 'setup.html'));

    // Обработчик отмены
    ipcMain.once('setup-cancel', () => {
      logDiag('Установка отменена пользователем');
      if (setupWindow && !setupWindow.isDestroyed()) setupWindow.close();
      app.quit();
    });

    setupWindow.on('closed', () => { setupWindow = null; });

    setupWindow.once('ready-to-show', () => {
      setupWindow.show();
      setupWindow.center();

      // Запускаем установку
      depManager.installAll((step, percent, message) => {
        if (setupWindow && !setupWindow.isDestroyed()) {
          setupWindow.webContents.send('setup-progress', step, percent, message);
        }
        logDiag(`setup [${step}] ${percent}%`, { message });
      }).then(() => {
        logDiag('Установка зависимостей завершена');
        if (setupWindow && !setupWindow.isDestroyed()) {
          setupWindow.webContents.send('setup-complete');
          // Даём пользователю увидеть сообщение «Установка завершена»
          setTimeout(() => {
            if (setupWindow && !setupWindow.isDestroyed()) setupWindow.close();
            resolve();
          }, 1500);
        } else {
          resolve();
        }
      }).catch((err) => {
        logDiag('Ошибка установки зависимостей', { error: err.message });
        if (setupWindow && !setupWindow.isDestroyed()) {
          setupWindow.webContents.send('setup-error', err.message);
        }
        // Не reject — пусть пользователь закроет окно вручную
      });
    });
  });
}

/** Показывает окно загрузки моделей. Возвращает Promise<void> по завершении. */
function runModelsDownloadWindow(modelsStatus) {
  return new Promise((resolve) => {
    const modelsWindow = new BrowserWindow({
      width: 520,
      height: 340,
      resizable: false,
      frame: false,
      backgroundColor: '#1A1A1A',
      webPreferences: {
        nodeIntegration: false,
        contextIsolation: true,
        preload: path.join(__dirname, 'preload-setup.js'),
      },
      icon: process.platform === 'win32' ? path.join(__dirname, '../build/icon.ico') : undefined,
    });

    modelsWindow.loadFile(path.join(__dirname, 'setup-models.html'));

    ipcMain.once('setup-cancel', () => {
      logDiag('Загрузка моделей пропущена пользователем');
      if (modelsWindow && !modelsWindow.isDestroyed()) modelsWindow.close();
      resolve();
    });

    modelsWindow.on('closed', () => { resolve(); });

    modelsWindow.once('ready-to-show', () => {
      modelsWindow.show();
      modelsWindow.center();

      // Отправляем начальное состояние
      if (modelsStatus.whisperOk) {
        modelsWindow.webContents.send('setup-progress', 'models-whisper', 100, 'Модель Whisper уже загружена');
      }
      if (modelsStatus.ttsOk) {
        modelsWindow.webContents.send('setup-progress', 'models-tts', 100, 'Модели F5-TTS уже загружены');
      }

      depManager.downloadModels((step, pct, msg) => {
        if (modelsWindow && !modelsWindow.isDestroyed()) {
          modelsWindow.webContents.send('setup-progress', step, pct, msg);
        }
        logDiag(`models [${step}] ${pct}%`, { message: msg });
      }).then(() => {
        logDiag('Загрузка моделей завершена');
        if (modelsWindow && !modelsWindow.isDestroyed()) {
          modelsWindow.webContents.send('setup-complete');
          setTimeout(() => {
            if (modelsWindow && !modelsWindow.isDestroyed()) modelsWindow.close();
          }, 1500);
        }
      }).catch((err) => {
        logDiag('Ошибка загрузки моделей', { error: err.message });
        if (modelsWindow && !modelsWindow.isDestroyed()) {
          modelsWindow.webContents.send('setup-error', err.message);
        }
      });
    });
  });
}

// --- Запуск API сервера и ожидание готовности ---

async function launchBackend() {
  logDiag('--- launchBackend: проверка API ---');
  const healthResult = await checkApiServer();
  const apiRunning = healthResult.ok;
  logDiag('начальная проверка API', { apiRunning, port: apiPort, statusCode: healthResult.statusCode, error: healthResult.errorMessage });

  if (!apiRunning) {
    logDiag('API не запущен — запускаем backend');
    console.log('Запуск API сервера из Electron...');
    startApiServer();
    if (apiPortFilePath) {
      const portFromFile = await waitForPortFile(apiPortFilePath);
      logDiag('результат waitForPortFile', { portFromFile, apiPortFilePath });
      if (portFromFile) {
        apiPort = portFromFile;
        if (apiPort !== 5001) {
          console.log(`⏳ Порт 5001 занят, backend использует порт ${apiPort}`);
        }
      } else {
        logDiag('portFromFile пустой или 0, оставляем apiPort', { apiPort });
      }
    } else {
      logDiag('apiPortFilePath не задан (startApiServer не вызван или вышел по return)');
    }
    try {
      await new Promise(r => setTimeout(r, 400));
      console.log('⏳ Ожидание готовности API сервера...');
      await waitForApiServer(60, 500);
      console.log('✅ API сервер успешно запущен и готов к работе');
    } catch (error) {
      const logPath = app.isPackaged ? getDiagnosticLogPath() : '';
      logDiag('ERROR: waitForApiServer таймаут', {
        apiPort,
        apiPortFilePath,
        portFileExists: apiPortFilePath ? fs.existsSync(apiPortFilePath) : false,
        portFileContent: apiPortFilePath && fs.existsSync(apiPortFilePath) ? fs.readFileSync(apiPortFilePath, 'utf8').trim() : '',
        processAlive: apiServer && !apiServer.killed,
        pid: apiServer ? apiServer.pid : null,
        stderrLength: apiServer && apiServer._stderrBuffer ? apiServer._stderrBuffer.join('').length : 0,
        diagnosticLogPath: logPath,
      });
      console.error('❌ Не удалось дождаться запуска API сервера:', error);
      const { dialog } = require('electron');
      const logHint = logPath ? `\n\nПодробный лог: ${logPath}` : '';
      dialog.showErrorBox(
        'Предупреждение: API сервер не отвечает',
        `API сервер не стал доступен за отведенное время.\n\n` +
        `Возможные причины:\n` +
        `1. Сервер еще запускается (подождите несколько секунд)\n` +
        `2. Порт занят другим приложением\n` +
        `3. Ошибка при запуске Python backend\n\n` +
        `Попробуйте перезапустить приложение.${logHint}`
      );
    }
  } else {
    logDiag('API уже запущен, пропускаем startApiServer');
    console.log('API сервер уже запущен, пропускаем запуск из Electron');
  }
}

// Когда Electron готов
app.whenReady().then(async () => {
  logDiag('--- app.whenReady ---');

  // Проверяем зависимости (Python, pip, пакеты, FFmpeg)
  const deps = depManager.checkDependencies();
  logDiag('Состояние зависимостей', deps);

  if (!deps.allOk) {
    logDiag('Зависимости не установлены — запускаем окно установки');
    try {
      await runSetupWindow();
    } catch (err) {
      logDiag('Ошибка в runSetupWindow', { error: err.message });
    }
    // Перепроверяем после установки
    const depsAfter = depManager.checkDependencies();
    if (!depsAfter.allOk) {
      logDiag('Зависимости всё ещё не установлены после setup', depsAfter);
      const { dialog } = require('electron');
      dialog.showErrorBox(
        'Зависимости не установлены',
        'Не удалось установить необходимые компоненты.\n\n' +
        'Проверьте подключение к интернету и перезапустите приложение.'
      );
      app.quit();
      return;
    }
    // Инициализируем SHA для будущих обновлений (первая установка)
    try {
      await autoUpdater.initializeUpdateSha();
    } catch (err) {
      logDiag('initializeUpdateSha failed (non-critical)', { error: err.message });
    }
  }

  // Проверяем обновления Python-кода из GitHub
  try {
    const updateResult = await autoUpdater.checkForPythonUpdate((step, pct, msg) => {
      logDiag(`[${step}] ${pct}%`, { message: msg });
    });
    if (updateResult.updated) {
      logDiag('Python sources updated', {
        from: updateResult.fromSha.substring(0, 8),
        to: updateResult.toSha.substring(0, 8),
        files: updateResult.changedFiles.length,
        requirementsChanged: updateResult.requirementsChanged,
      });
      if (updateResult.requirementsChanged) {
        logDiag('requirements.txt changed — running pip install...');
        await autoUpdater.installUpdatedRequirements((step, pct, msg) => {
          logDiag(`[${step}] ${pct}%`, { message: msg });
        });
      }
    }
  } catch (err) {
    logDiag('Auto-update check failed (non-critical)', { error: err.message });
  }

  // Проверяем и доустанавливаем Python-зависимости (если requirements.txt изменился)
  try {
    await depManager.ensureRequirements((step, pct, msg) => {
      logDiag(`[${step}] ${pct}%`, { message: msg });
    });
  } catch (err) {
    logDiag('ensureRequirements failed (non-critical)', { error: err.message });
  }

  // Проверяем наличие моделей (Whisper + F5-TTS)
  const modelsStatus = depManager.checkModels();
  logDiag('Состояние моделей', modelsStatus);

  if (!modelsStatus.allOk) {
    logDiag('Модели не загружены — показываем окно загрузки');
    try {
      await runModelsDownloadWindow(modelsStatus);
    } catch (err) {
      logDiag('Model download window error (non-critical)', { error: err.message });
    }
  }

  // Запускаем backend
  await launchBackend();

  createWindow();
  appStarting = false;

  // Проверка обновлений Electron-оболочки (GitHub Releases)
  if (app.isPackaged) {
    try {
      const { autoUpdater: electronUpdater } = require('electron-updater');
      electronUpdater.autoDownload = true;
      electronUpdater.autoInstallOnAppQuit = true;
      electronUpdater.logger = { info: (m) => logDiag('electron-updater', { info: m }), warn: (m) => logDiag('electron-updater', { warn: m }), error: (m) => logDiag('electron-updater', { error: m }) };

      electronUpdater.on('update-available', (info) => {
        logDiag('Доступно обновление приложения', { version: info.version });
      });

      electronUpdater.on('update-downloaded', (info) => {
        logDiag('Обновление приложения скачано', { version: info.version });
        // Нативный диалог — не зависит от React UI
        dialog.showMessageBox(mainWindow, {
          type: 'info',
          title: 'Обновление готово',
          message: `Доступна новая версия ${info.version}.\nОбновление уже скачано. Перезапустить приложение для установки?`,
          buttons: ['Перезапустить', 'Позже'],
          defaultId: 0,
          cancelId: 1,
        }).then(({ response }) => {
          if (response === 0) {
            electronUpdater.quitAndInstall(false, true);
          }
        });
      });

      electronUpdater.checkForUpdates().catch(err => {
        logDiag('electron-updater check failed (non-critical)', { error: err.message });
      });
    } catch (err) {
      logDiag('electron-updater init failed', { error: err.message });
    }
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

// Выход когда все окна закрыты
app.on('window-all-closed', () => {
  // Не выходим если приложение ещё запускается (setup/models окно закрылось, но mainWindow ещё не создано)
  if (appStarting) return;

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
      hostname: '127.0.0.1',
      port: apiPort,
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
