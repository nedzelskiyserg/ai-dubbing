/**
 * Менеджер зависимостей — скачивает и устанавливает Python, pip, пакеты и FFmpeg
 * при первом запуске приложения. Всё ставится в %LOCALAPPDATA%/AI Dubbing Studio/.
 */
const path = require('path');
const fs = require('fs');
const os = require('os');
const https = require('https');
const http = require('http');
const { execFile, spawn } = require('child_process');
const { createWriteStream } = require('fs');

// --- Конфигурация ---
const PYTHON_VERSION = '3.10.11';
const PYTHON_URL = `https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-embed-amd64.zip`;
const GET_PIP_URL = 'https://bootstrap.pypa.io/get-pip.py';
const FFMPEG_URL = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip';
const TORCH_INDEX_URL = 'https://download.pytorch.org/whl/cpu';

// Базовая директория для всех зависимостей
function getBaseDir() {
  const localAppData = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
  return path.join(localAppData, 'AI Dubbing Studio');
}

function getPythonDir() { return path.join(getBaseDir(), 'python'); }
function getPythonExe() { return path.join(getPythonDir(), 'python.exe'); }
function getPipExe() { return path.join(getPythonDir(), 'Scripts', 'pip.exe'); }
function getFFmpegDir() { return path.join(getBaseDir(), 'ffmpeg'); }
function getFFmpegExe() { return path.join(getFFmpegDir(), 'ffmpeg.exe'); }
function getSetupMarker() { return path.join(getBaseDir(), '.setup-complete'); }

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

// --- Проверка зависимостей ---

/** Возвращает объект с состоянием каждой зависимости. */
function checkDependencies() {
  const result = {
    pythonOk: fs.existsSync(getPythonExe()),
    pipOk: fs.existsSync(getPipExe()),
    packagesOk: false,
    ffmpegOk: fs.existsSync(getFFmpegExe()),
    allOk: false,
  };
  // Маркер .setup-complete означает что все пакеты были установлены
  result.packagesOk = result.pipOk && fs.existsSync(getSetupMarker());
  result.allOk = result.pythonOk && result.pipOk && result.packagesOk && result.ffmpegOk;
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
  const baseDir = getBaseDir();
  fs.mkdirSync(baseDir, { recursive: true });

  const status = checkDependencies();
  const tempDir = path.join(baseDir, '_temp');
  fs.mkdirSync(tempDir, { recursive: true });

  const log = (step, pct, msg) => {
    if (onProgress) onProgress(step, pct, msg);
  };

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
  if (!status.packagesOk) {
    // 3a. Сначала ставим PyTorch CPU (меньший размер)
    log('packages', 0, 'Установка PyTorch (CPU)... Это может занять несколько минут.');
    await runProcess(getPythonExe(), [
      '-m', 'pip', 'install',
      'torch', 'torchaudio',
      '--index-url', TORCH_INDEX_URL,
      '--no-cache-dir',
      '--no-warn-script-location',
    ], {
      cwd: getPythonDir(),
      env: { ...process.env, PYTHONUTF8: '1' },
      timeout: 600000,
    }, (line) => {
      const trimmed = line.trim();
      if (trimmed) log('packages', 20, trimmed);
    });
    log('packages', 30, 'PyTorch установлен. Установка остальных зависимостей...');

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
  getBaseDir,
  getPythonDir,
  getPythonExe,
  getFFmpegDir,
  getFFmpegExe,
  getSrcPath,
  getRequirementsPath,
};
