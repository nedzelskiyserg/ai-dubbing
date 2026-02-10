/**
 * Менеджер зависимостей — скачивает и устанавливает Python, pip, пакеты и FFmpeg
 * при первом запуске приложения.
 * Пути зависят от ОС: Windows — %LOCALAPPDATA%, macOS — ~/Library/Application Support, Linux — ~/.config.
 */
const path = require('path');
const fs = require('fs');
const os = require('os');
const https = require('https');
const http = require('http');
const { execFile, spawn } = require('child_process');
const { createWriteStream } = require('fs');

const APP_NAME = 'AI Dubbing Studio';
const isWindows = process.platform === 'win32';
const isDarwin = process.platform === 'darwin';

// --- Конфигурация ---
const PYTHON_VERSION = '3.10.11';
const PYTHON_URL = `https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-embed-amd64.zip`;
const GET_PIP_URL = 'https://bootstrap.pypa.io/get-pip.py';
const FFMPEG_URL = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip';
const TORCH_INDEX_CPU = 'https://download.pytorch.org/whl/cpu';
const TORCH_INDEX_CUDA = 'https://download.pytorch.org/whl/cu124';  // CUDA 12.4
// VC++ Redistributable 2015-2022 (x64) — обязателен для PyTorch (c10.dll и др.)
const VCREDIST_URL = 'https://aka.ms/vs/17/release/vc_redist.x64.exe';

// Базовая директория для всех зависимостей (платформо-зависимая)
function getBaseDir() {
  if (isWindows) {
    const localAppData = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
    return path.join(localAppData, APP_NAME);
  }
  if (isDarwin) {
    return path.join(os.homedir(), 'Library', 'Application Support', APP_NAME);
  }
  return path.join(os.homedir(), '.config', APP_NAME);
}

function getPythonDir() { return path.join(getBaseDir(), 'python'); }
function getPythonExe() {
  return path.join(getPythonDir(), isWindows ? 'python.exe' : 'bin/python3');
}
function getPipExe() {
  return path.join(getPythonDir(), isWindows ? 'Scripts/pip.exe' : 'bin/pip3');
}
function getFFmpegDir() { return path.join(getBaseDir(), 'ffmpeg'); }
function getFFmpegExe() {
  return path.join(getFFmpegDir(), isWindows ? 'ffmpeg.exe' : 'ffmpeg');
}
function getSetupMarker() { return path.join(getBaseDir(), '.setup-complete'); }
function getVcRedistMarker() { return path.join(getBaseDir(), '.vcredist-installed'); }
function getTorchVariantMarker() { return path.join(getBaseDir(), '.torch-variant'); }

// --- Утилиты ---

/** Скачивает файл по URL с поддержкой редиректов. Вызывает onProgress(downloaded, total). */
function downloadFile(url, destPath, onProgress) {
  return new Promise((resolve, reject) => {
    const file = createWriteStream(destPath);
    const makeRequest = (currentUrl, redirectCount = 0) => {
      if (redirectCount > 5) {
        file.close();
        return reject(new Error('Too many redirects'));
      }
      const lib = currentUrl.startsWith('https') ? https : http;
      lib.get(currentUrl, (response) => {
        if (response.statusCode >= 300 && response.statusCode < 400 && response.headers.location) {
          response.resume();
          return makeRequest(response.headers.location, redirectCount + 1);
        }
        if (response.statusCode !== 200) {
          file.close();
          fs.unlinkSync(destPath);
          return reject(new Error(`HTTP ${response.statusCode} при скачивании ${currentUrl}`));
        }
        const total = parseInt(response.headers['content-length'] || '0', 10);
        let downloaded = 0;
        response.on('data', (chunk) => {
          downloaded += chunk.length;
          if (onProgress) onProgress(downloaded, total);
        });
        response.pipe(file);
        file.on('finish', () => { file.close(resolve); });
        file.on('error', (err) => {
          fs.unlinkSync(destPath);
          reject(err);
        });
      }).on('error', (err) => {
        file.close();
        if (fs.existsSync(destPath)) fs.unlinkSync(destPath);
        reject(err);
      });
    };
    makeRequest(url);
  });
}

/** Распаковывает zip через PowerShell (доступен на всех Windows 10+). */
function unzip(zipPath, destDir) {
  return new Promise((resolve, reject) => {
    fs.mkdirSync(destDir, { recursive: true });
    const cmd = `Expand-Archive -LiteralPath '${zipPath}' -DestinationPath '${destDir}' -Force`;
    execFile('powershell.exe', ['-NoProfile', '-Command', cmd], { timeout: 300000 }, (err, stdout, stderr) => {
      if (err) return reject(new Error(`Ошибка распаковки: ${stderr || err.message}`));
      resolve();
    });
  });
}

/** Запускает процесс и возвращает Promise. Вызывает onOutput(line) для stdout+stderr. */
function runProcess(exe, args, opts, onOutput) {
  return new Promise((resolve, reject) => {
    const proc = spawn(exe, args, { ...opts, stdio: 'pipe' });
    let stderr = '';
    if (proc.stdout && onOutput) proc.stdout.on('data', (d) => onOutput(d.toString()));
    if (proc.stderr) {
      proc.stderr.on('data', (d) => {
        stderr += d.toString();
        if (onOutput) onOutput(d.toString());
      });
    }
    proc.on('error', (err) => reject(err));
    proc.on('close', (code) => {
      if (code !== 0) return reject(new Error(`Процесс завершился с кодом ${code}: ${stderr.slice(-500)}`));
      resolve();
    });
  });
}

// --- Детект NVIDIA GPU ---

/**
 * Определяет наличие NVIDIA GPU через nvidia-smi.
 * Возвращает { detected: bool, name: string|null }.
 */
function detectNvidiaGpu() {
  return new Promise((resolve) => {
    if (!isWindows) {
      return resolve({ detected: false, name: null });
    }
    // nvidia-smi всегда доступна при установленных драйверах NVIDIA
    execFile('nvidia-smi', ['--query-gpu=name', '--format=csv,noheader,nounits'], { timeout: 10000 }, (err, stdout) => {
      if (err || !stdout || !stdout.trim()) {
        // Fallback: WMIC (устаревший, но работает на Windows 10)
        execFile('cmd.exe', ['/c', 'wmic path win32_videocontroller get name /format:list'], { timeout: 10000 }, (err2, stdout2) => {
          if (!err2 && stdout2 && /nvidia/i.test(stdout2)) {
            const match = stdout2.match(/Name=(.+)/i);
            return resolve({ detected: true, name: match ? match[1].trim() : 'NVIDIA GPU' });
          }
          return resolve({ detected: false, name: null });
        });
        return;
      }
      const gpuName = stdout.trim().split('\n')[0].trim();
      resolve({ detected: true, name: gpuName });
    });
  });
}

/**
 * Возвращает нужный вариант PyTorch ('cpu' или 'cu124') и URL индекса.
 */
async function getTorchConfig() {
  const gpu = await detectNvidiaGpu();
  if (gpu.detected) {
    return { variant: 'cu124', indexUrl: TORCH_INDEX_CUDA, gpuName: gpu.name };
  }
  return { variant: 'cpu', indexUrl: TORCH_INDEX_CPU, gpuName: null };
}

/**
 * Проверяет, совпадает ли установленный вариант PyTorch с текущим оборудованием.
 * Если GPU появился после установки CPU-версии, нужно переустановить PyTorch.
 */
function getInstalledTorchVariant() {
  const marker = getTorchVariantMarker();
  if (fs.existsSync(marker)) {
    return fs.readFileSync(marker, 'utf8').trim();
  }
  return null;
}

// --- Проверка зависимостей ---

/** Возвращает объект с состоянием каждой зависимости. */
function checkDependencies() {
  // macOS/Linux в режиме разработки: Python и FFmpeg берутся из .venv или PATH (main.js), установка не нужна
  if (!isWindows) {
    const { app } = require('electron');
    if (!app.isPackaged) {
      return {
        vcredistOk: true,
        pythonOk: true,
        pipOk: true,
        packagesOk: true,
        ffmpegOk: true,
        allOk: true,
      };
    }
  }

  // Windows или собранное приложение (packaged)
  const vcredistOk = isWindows ? fs.existsSync(getVcRedistMarker()) : true;
  const result = {
    vcredistOk,
    pythonOk: fs.existsSync(getPythonExe()),
    pipOk: fs.existsSync(getPipExe()),
    packagesOk: false,
    ffmpegOk: fs.existsSync(getFFmpegExe()),
    allOk: false,
  };
  result.packagesOk = result.pipOk && fs.existsSync(getSetupMarker());
  result.allOk = result.vcredistOk && result.pythonOk && result.pipOk && result.packagesOk && result.ffmpegOk;
  return result;
}

// --- Установка ---

/**
 * Полная установка всех зависимостей.
 * @param {function} onProgress — (step, percent, message) callback
 *   step: 'python' | 'pip' | 'packages' | 'ffmpeg'
 *   percent: 0-100
 *   message: строка для лога
 * @returns {Promise<void>}
 */
async function installAll(onProgress) {
  const log = (step, pct, msg) => {
    if (onProgress) onProgress(step, pct, msg);
  };

  // Установка через этот скрипт только для Windows; на macOS/Linux используйте системный Python и FFmpeg
  if (!isWindows) {
    log('packages', 100, isDarwin
      ? 'На macOS используйте Python из .venv или brew (brew install python ffmpeg).'
      : 'На Linux используйте системный Python и FFmpeg.');
    return;
  }

  const baseDir = getBaseDir();
  fs.mkdirSync(baseDir, { recursive: true });

  const status = checkDependencies();
  const tempDir = path.join(baseDir, '_temp');
  fs.mkdirSync(tempDir, { recursive: true });

  // 0. Visual C++ Redistributable (обязателен для PyTorch — c10.dll, torch_cpu.dll и др.)
  if (!status.vcredistOk) {
    // Сначала проверяем через реестр — возможно VC++ уже установлен в системе
    const vcAlreadyInstalled = await checkVcRedistInstalled();
    if (vcAlreadyInstalled) {
      log('vcredist', 100, 'Visual C++ Redistributable уже установлен в системе');
      fs.writeFileSync(getVcRedistMarker(), 'system', 'utf8');
    } else {
      log('vcredist', 0, 'Скачивание Visual C++ Redistributable...');
      const vcExePath = path.join(tempDir, 'vc_redist.x64.exe');
      await downloadFile(VCREDIST_URL, vcExePath, (dl, total) => {
        const pct = total ? Math.round((dl / total) * 100) : 0;
        log('vcredist', pct, `Скачивание VC++ Redistributable... ${formatBytes(dl)} / ${formatBytes(total)}`);
      });
      log('vcredist', 80, 'Установка Visual C++ Redistributable (может появиться запрос прав)...');
      // Запуск с повышением прав через PowerShell Start-Process -Verb RunAs
      // vc_redist поддерживает /install /quiet /norestart
      // Коды: 0 = OK, 1638 = уже установлена новая, 3010 = OK но нужен reboot
      await new Promise((resolve, reject) => {
        const psCmd = `Start-Process -FilePath '${vcExePath}' -ArgumentList '/install','/quiet','/norestart' -Verb RunAs -Wait -PassThru | ForEach-Object { exit $_.ExitCode }`;
        execFile('powershell.exe', ['-NoProfile', '-Command', psCmd], { timeout: 180000 }, (err, stdout, stderr) => {
          // Любой результат кроме реальной ошибки запуска — считаем успехом
          // (пользователь мог отменить UAC — тогда будет ошибка, но мы попробуем продолжить)
          if (err && err.code !== 0 && err.code !== 1638 && err.code !== 3010) {
            // Проверяем, может VC++ всё-таки уже стоит (пользователь мог отменить UAC,
            // но VC++ уже был установлен через другой софт)
            log('vcredist', 90, 'Проверка установки VC++ после попытки...');
          }
          resolve();
        });
      });
      try { fs.unlinkSync(vcExePath); } catch (e) { /* ignore */ }

      // Финальная проверка — удалось ли установить
      const vcNowInstalled = await checkVcRedistInstalled();
      if (vcNowInstalled) {
        fs.writeFileSync(getVcRedistMarker(), new Date().toISOString(), 'utf8');
        log('vcredist', 100, 'Visual C++ Redistributable установлен');
      } else {
        // Не критично — может быть установлен частично или пользователь отменил UAC.
        // PyTorch может и так работать если DLL есть в системе.
        log('vcredist', 100, 'VC++ Redistributable: установка не подтверждена, продолжаем...');
        fs.writeFileSync(getVcRedistMarker(), 'attempted', 'utf8');
      }
    }
  } else {
    log('vcredist', 100, 'Visual C++ Redistributable уже установлен');
  }

  // 1. Python
  if (!status.pythonOk) {
    log('python', 0, 'Скачивание Python...');
    const zipPath = path.join(tempDir, 'python-embed.zip');
    await downloadFile(PYTHON_URL, zipPath, (dl, total) => {
      const pct = total ? Math.round((dl / total) * 100) : 0;
      log('python', pct, `Скачивание Python... ${formatBytes(dl)} / ${formatBytes(total)}`);
    });
    log('python', 100, 'Распаковка Python...');
    await unzip(zipPath, getPythonDir());
    fs.unlinkSync(zipPath);

    // Раскомментируем "import site" в python310._pth для работы pip
    const pthFile = path.join(getPythonDir(), `python310._pth`);
    if (fs.existsSync(pthFile)) {
      let content = fs.readFileSync(pthFile, 'utf8');
      content = content.replace(/^#\s*import site/m, 'import site');
      // Добавляем путь к site-packages если его нет
      if (!content.includes('Lib\\site-packages')) {
        content += '\nLib\\site-packages\n';
      }
      fs.writeFileSync(pthFile, content, 'utf8');
    }
    log('python', 100, 'Python установлен');
  } else {
    log('python', 100, 'Python уже установлен');
  }

  // 2. pip
  if (!status.pipOk) {
    log('pip', 0, 'Скачивание pip...');
    const getPipPath = path.join(tempDir, 'get-pip.py');
    await downloadFile(GET_PIP_URL, getPipPath, (dl, total) => {
      const pct = total ? Math.round((dl / total) * 100) : 0;
      log('pip', pct, `Скачивание pip... ${formatBytes(dl)}`);
    });
    log('pip', 50, 'Установка pip...');
    await runProcess(getPythonExe(), [getPipPath, '--no-warn-script-location'], {
      cwd: getPythonDir(),
      env: { ...process.env, PYTHONUTF8: '1' },
    }, (line) => {
      log('pip', 50, line.trim());
    });
    fs.unlinkSync(getPipPath);
    log('pip', 100, 'pip установлен');
  } else {
    log('pip', 100, 'pip уже установлен');
  }

  // 3. Python пакеты

  // Определяем нужный вариант PyTorch (CPU или CUDA) по наличию NVIDIA GPU
  const torchConfig = await getTorchConfig();
  const installedVariant = getInstalledTorchVariant();
  // Переустановка нужна если: (1) вариант изменился, или (2) маркера нет + пакеты есть + найден GPU
  // Случай (2) — апгрейд старой установки (CPU) при наличии GPU
  const needTorchReinstall = installedVariant
    ? installedVariant !== torchConfig.variant
    : (status.packagesOk && torchConfig.variant === 'cu124');

  if (needTorchReinstall) {
    // GPU появился (или исчез) после первой установки — переустанавливаем PyTorch
    const fromTo = `${installedVariant} → ${torchConfig.variant}`;
    const reason = torchConfig.gpuName
      ? `Обнаружена NVIDIA GPU: ${torchConfig.gpuName}`
      : 'NVIDIA GPU не обнаружен';
    log('packages', 0, `${reason}. Переустановка PyTorch (${fromTo})...`);
    await runProcess(getPythonExe(), [
      '-m', 'pip', 'install',
      'torch', 'torchaudio',
      '--index-url', torchConfig.indexUrl,
      '--force-reinstall',
      '--no-cache-dir',
      '--no-warn-script-location',
    ], {
      cwd: getPythonDir(),
      env: getPythonEnv(),
      timeout: 900000,
    }, (line) => {
      const trimmed = line.trim();
      if (trimmed) log('packages', 20, trimmed);
    });
    fs.writeFileSync(getTorchVariantMarker(), torchConfig.variant, 'utf8');
    log('packages', 30, `PyTorch (${torchConfig.variant}) переустановлен.`);
  }

  if (!status.packagesOk) {
    // 3a. Ставим PyTorch с поддержкой GPU (если есть NVIDIA) или CPU
    if (torchConfig.gpuName) {
      log('packages', 0, `Обнаружена NVIDIA GPU: ${torchConfig.gpuName}. Установка PyTorch с CUDA... Это может занять несколько минут.`);
    } else {
      log('packages', 0, 'NVIDIA GPU не обнаружен. Установка PyTorch (CPU)... Это может занять несколько минут.');
    }
    await runProcess(getPythonExe(), [
      '-m', 'pip', 'install',
      'torch', 'torchaudio',
      '--index-url', torchConfig.indexUrl,
      '--no-cache-dir',
      '--no-warn-script-location',
    ], {
      cwd: getPythonDir(),
      env: { ...process.env, PYTHONUTF8: '1' },
      timeout: 900000,  // 15 мин (CUDA-версия ~2.5 ГБ)
    }, (line) => {
      const trimmed = line.trim();
      if (trimmed) log('packages', 20, trimmed);
    });
    fs.writeFileSync(getTorchVariantMarker(), torchConfig.variant, 'utf8');
    log('packages', 30, `PyTorch (${torchConfig.variant}) установлен. Установка остальных зависимостей...`);

    // 3b. Определяем путь к requirements.txt
    const reqPath = getRequirementsPath();
    if (!reqPath) {
      throw new Error('requirements.txt не найден! Переустановите приложение.');
    }

    // 3c. Ставим все остальные зависимости (torch уже установлен, pip не будет его перекачивать)
    await runProcess(getPythonExe(), [
      '-m', 'pip', 'install',
      '-r', reqPath,
      '--no-cache-dir',
      '--no-warn-script-location',
    ], {
      cwd: getPythonDir(),
      env: { ...process.env, PYTHONUTF8: '1' },
      timeout: 900000,
    }, (line) => {
      const trimmed = line.trim();
      if (trimmed) log('packages', 60, trimmed);
    });

    // Записываем маркер успешной установки
    fs.writeFileSync(getSetupMarker(), new Date().toISOString(), 'utf8');
    log('packages', 100, 'Все Python-пакеты установлены');
  } else {
    log('packages', 100, 'Python-пакеты уже установлены');
  }

  // 3.5. Предзагрузка модели Whisper large-v3 (~3 ГБ) в папку приложения
  const srcPath = getSrcPath();
  const downloadModelsScript = srcPath ? path.join(srcPath, 'download_models.py') : null;
  if (downloadModelsScript && fs.existsSync(downloadModelsScript)) {
    log('models', 0, 'Скачивание модели Whisper large-v3 (~3 ГБ). Это может занять несколько минут...');
    try {
      await runProcess(getPythonExe(), [downloadModelsScript], {
        cwd: srcPath,
        env: getPythonEnv(),
        timeout: 600000, // 10 мин
      }, (line) => {
        const trimmed = line.trim();
        if (trimmed) log('models', 50, trimmed);
      });
      log('models', 100, 'Модель large-v3 загружена');
    } catch (err) {
      log('models', 100, 'Предзагрузка модели пропущена (модель скачается при первом запуске транскрипции). ' + (err.message || ''));
    }
  } else {
    log('models', 100, 'Скрипт предзагрузки не найден, модель скачается при первом запуске');
  }

  // 3.6. Предзагрузка модели XTTS v2 (~1.8 ГБ) для озвучки
  const downloadTtsScript = srcPath ? path.join(srcPath, 'download_tts_model.py') : null;
  if (downloadTtsScript && fs.existsSync(downloadTtsScript)) {
    log('models', 0, 'Скачивание модели XTTS v2 (~1.8 ГБ) для озвучки. Это может занять несколько минут...');
    try {
      await runProcess(getPythonExe(), [downloadTtsScript], {
        cwd: srcPath,
        env: getPythonEnv(),
        timeout: 900000, // 15 мин (модель ~1.8 ГБ + retry)
      }, (line) => {
        const trimmed = line.trim();
        if (trimmed) log('models', 75, trimmed);
      });
      log('models', 100, 'Модель XTTS v2 загружена');
    } catch (err) {
      log('models', 100, 'Предзагрузка XTTS пропущена (модель скачается при первом запуске озвучки). ' + (err.message || ''));
    }
  }

  // 4. FFmpeg
  if (!status.ffmpegOk) {
    log('ffmpeg', 0, 'Скачивание FFmpeg...');
    const zipPath = path.join(tempDir, 'ffmpeg.zip');
    await downloadFile(FFMPEG_URL, zipPath, (dl, total) => {
      const pct = total ? Math.round((dl / total) * 100) : 0;
      log('ffmpeg', pct, `Скачивание FFmpeg... ${formatBytes(dl)} / ${formatBytes(total)}`);
    });
    log('ffmpeg', 80, 'Распаковка FFmpeg...');
    const ffmpegTemp = path.join(tempDir, 'ffmpeg_extracted');
    await unzip(zipPath, ffmpegTemp);
    fs.unlinkSync(zipPath);

    // Находим ffmpeg.exe в распакованных файлах
    const ffmpegExeFound = findFileRecursive(ffmpegTemp, 'ffmpeg.exe');
    if (!ffmpegExeFound) {
      throw new Error('ffmpeg.exe не найден в архиве');
    }
    fs.mkdirSync(getFFmpegDir(), { recursive: true });
    fs.copyFileSync(ffmpegExeFound, getFFmpegExe());

    // Копируем DLL файлы рядом с ffmpeg.exe если есть
    const ffmpegSrcDir = path.dirname(ffmpegExeFound);
    const dlls = fs.readdirSync(ffmpegSrcDir).filter(f => f.endsWith('.dll'));
    for (const dll of dlls) {
      fs.copyFileSync(path.join(ffmpegSrcDir, dll), path.join(getFFmpegDir(), dll));
    }

    // Очищаем временную папку ffmpeg
    fs.rmSync(ffmpegTemp, { recursive: true, force: true });

    log('ffmpeg', 100, 'FFmpeg установлен');
  } else {
    log('ffmpeg', 100, 'FFmpeg уже установлен');
  }

  // Очищаем temp
  try { fs.rmSync(tempDir, { recursive: true, force: true }); } catch (e) { /* ignore */ }
}

// --- Вспомогательные ---

/** Проверяет наличие VC++ Redistributable 2015-2022 (x64) через реестр. */
function checkVcRedistInstalled() {
  return new Promise((resolve) => {
    // Проверяем реестр: HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64
    const regQuery = 'reg query "HKLM\\SOFTWARE\\Microsoft\\VisualStudio\\14.0\\VC\\Runtimes\\x64" /v Installed 2>nul';
    execFile('cmd.exe', ['/c', regQuery], { timeout: 5000 }, (err, stdout) => {
      if (!err && stdout && stdout.includes('0x1')) {
        resolve(true);
      } else {
        resolve(false);
      }
    });
  });
}

/** Ищет файл рекурсивно в директории */
function findFileRecursive(dir, filename) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      const found = findFileRecursive(fullPath, filename);
      if (found) return found;
    } else if (entry.name.toLowerCase() === filename.toLowerCase()) {
      return fullPath;
    }
  }
  return null;
}

/** Определяет путь к requirements.txt (в packaged приложении — в resources/) */
function getRequirementsPath() {
  const { app } = require('electron');
  const candidates = [];
  if (app.isPackaged) {
    const resPath = process.resourcesPath || path.join(path.dirname(app.getPath('exe')), 'resources');
    candidates.push(path.join(resPath, 'requirements.txt'));
    candidates.push(path.join(resPath, 'app.asar.unpacked', 'requirements.txt'));
  }
  // Dev mode
  candidates.push(path.join(__dirname, '..', '..', 'requirements.txt'));
  candidates.push(path.join(__dirname, '..', 'requirements.txt'));

  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

/** Определяет путь к src/ (Python исходники) */
function getSrcPath() {
  const { app } = require('electron');
  const candidates = [];
  if (app.isPackaged) {
    const resPath = process.resourcesPath || path.join(path.dirname(app.getPath('exe')), 'resources');
    candidates.push(path.join(resPath, 'src'));
  }
  // Dev mode
  candidates.push(path.join(__dirname, '..', '..', 'src'));

  for (const p of candidates) {
    if (fs.existsSync(p) && fs.existsSync(path.join(p, 'api_server.py'))) return p;
  }
  return null;
}

/**
 * Возвращает env-объект с SSL-сертификатами для Python-процессов.
 * Embedded Python не имеет системных CA — указываем путь к certifi.
 */
function getPythonEnv() {
  const env = { ...process.env, PYTHONUTF8: '1' };
  // Ищем certifi CA bundle в site-packages
  const certifiPath = path.join(getPythonDir(), 'Lib', 'site-packages', 'certifi', 'cacert.pem');
  if (fs.existsSync(certifiPath)) {
    env.SSL_CERT_FILE = certifiPath;
    env.REQUESTS_CA_BUNDLE = certifiPath;
    env.CURL_CA_BUNDLE = certifiPath;
  }
  return env;
}

function formatBytes(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
}

module.exports = {
  checkDependencies,
  installAll,
  detectNvidiaGpu,
  getBaseDir,
  getPythonDir,
  getPythonExe,
  getFFmpegDir,
  getFFmpegExe,
  getSrcPath,
  getRequirementsPath,
};
