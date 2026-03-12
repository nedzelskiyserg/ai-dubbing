/**
 * Auto-updater — горячее обновление Python-кода из GitHub без пересборки EXE.
 *
 * При запуске приложения:
 *   1. Проверяет последний коммит на GitHub (ветка main)
 *   2. Сравнивает с локально сохранённым SHA
 *   3. Скачивает только изменённые файлы в src/ и requirements.txt
 *   4. Если requirements.txt изменился — запускает pip install
 *
 * Работает с публичными репозиториями без токена (60 req/hour).
 * Для приватных — передать GITHUB_TOKEN через .env.
 */
const path = require('path');
const fs = require('fs');
const https = require('https');
const { spawn } = require('child_process');

// --- Конфигурация ---
const GITHUB_OWNER = 'nedzelskiyserg';
const GITHUB_REPO = 'ai-dubbing';
const GITHUB_BRANCH = 'main';

// Файлы/папки, которые отслеживаем для обновления
const WATCHED_PATHS = ['src/', 'requirements.txt'];

// --- Пути ---
const depManager = require('./dependency-manager');

function getUpdateShaFile() {
  return path.join(depManager.getBaseDir(), '.update-sha');
}

function getUpdateLockFile() {
  return path.join(depManager.getBaseDir(), '.update-lock');
}

// --- Логирование ---
function log(msg, obj) {
  const line = `[auto-updater] ${msg}`;
  const full = obj !== undefined ? `${line} ${JSON.stringify(obj)}` : line;
  console.log(full);
}

// --- GitHub API ---

/**
 * HTTPS GET запрос, возвращает parsed JSON.
 * Обрабатывает редиректы и rate-limit.
 */
function httpsGetJson(url, token) {
  return new Promise((resolve, reject) => {
    const headers = {
      'User-Agent': 'AI-Dubbing-Studio-Updater',
      'Accept': 'application/vnd.github.v3+json',
    };
    if (token) headers['Authorization'] = `token ${token}`;

    const makeRequest = (currentUrl, redirects = 0) => {
      if (redirects > 5) return reject(new Error('Too many redirects'));

      https.get(currentUrl, { headers }, (res) => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          res.resume();
          return makeRequest(res.headers.location, redirects + 1);
        }

        // Rate limit
        if (res.statusCode === 403) {
          const remaining = res.headers['x-ratelimit-remaining'];
          if (remaining === '0') {
            res.resume();
            return resolve(null); // Rate limited — skip update
          }
        }

        if (res.statusCode !== 200) {
          res.resume();
          return resolve(null); // Non-critical — skip update
        }

        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          try {
            resolve(JSON.parse(data));
          } catch (e) {
            resolve(null);
          }
        });
      }).on('error', () => resolve(null)); // Network error — skip update
    };

    makeRequest(url);
  });
}

/**
 * Скачивает файл из raw.githubusercontent.com в destPath.
 */
function downloadRawFile(sha, filePath, destPath, token) {
  return new Promise((resolve, reject) => {
    const url = `https://raw.githubusercontent.com/${GITHUB_OWNER}/${GITHUB_REPO}/${sha}/${filePath}`;
    const headers = { 'User-Agent': 'AI-Dubbing-Studio-Updater' };
    if (token) headers['Authorization'] = `token ${token}`;

    // Создаём родительскую директорию
    const parentDir = path.dirname(destPath);
    fs.mkdirSync(parentDir, { recursive: true });

    const tmpPath = destPath + '.tmp';
    const file = fs.createWriteStream(tmpPath);

    const makeRequest = (currentUrl, redirects = 0) => {
      if (redirects > 5) {
        file.close();
        try { fs.unlinkSync(tmpPath); } catch (e) { /* ignore */ }
        return reject(new Error('Too many redirects'));
      }

      https.get(currentUrl, { headers }, (res) => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          res.resume();
          return makeRequest(res.headers.location, redirects + 1);
        }
        if (res.statusCode !== 200) {
          file.close();
          try { fs.unlinkSync(tmpPath); } catch (e) { /* ignore */ }
          return reject(new Error(`HTTP ${res.statusCode} for ${filePath}`));
        }
        res.pipe(file);
        file.on('finish', () => {
          file.close(() => {
            // Атомарная замена: tmp → target
            try {
              if (fs.existsSync(destPath)) fs.unlinkSync(destPath);
              fs.renameSync(tmpPath, destPath);
              resolve();
            } catch (e) {
              reject(e);
            }
          });
        });
      }).on('error', (err) => {
        file.close();
        try { fs.unlinkSync(tmpPath); } catch (e) { /* ignore */ }
        reject(err);
      });
    };

    makeRequest(url);
  });
}

// --- Маппинг путей ---

/**
 * Определяет локальный путь файла по его репо-пути (src/core/voice_cloner.py → resources/src/core/voice_cloner.py)
 */
function getTargetPath(relativePath) {
  const { app } = require('electron');
  if (app.isPackaged) {
    const resPath = process.resourcesPath || path.join(path.dirname(app.getPath('exe')), 'resources');
    return path.join(resPath, relativePath);
  }
  // Dev mode
  return path.join(__dirname, '../..', relativePath);
}

/**
 * Проверяет, относится ли файл к отслеживаемым путям.
 */
function isWatchedFile(filePath) {
  return WATCHED_PATHS.some(p => {
    if (p.endsWith('/')) return filePath.startsWith(p);
    return filePath === p;
  });
}

// --- Lock ---

function acquireLock() {
  const lockFile = getUpdateLockFile();
  try {
    if (fs.existsSync(lockFile)) {
      const content = fs.readFileSync(lockFile, 'utf8').trim();
      const pid = parseInt(content, 10);
      if (pid && pid !== process.pid) {
        // Проверяем, жив ли процесс
        try {
          process.kill(pid, 0);
          return false; // Процесс жив — другой экземпляр обновляется
        } catch (e) {
          // Процесс мёртв — удаляем stale lock
        }
      }
    }
    fs.writeFileSync(lockFile, String(process.pid), 'utf8');
    return true;
  } catch (e) {
    return false;
  }
}

function releaseLock() {
  try {
    const lockFile = getUpdateLockFile();
    if (fs.existsSync(lockFile)) fs.unlinkSync(lockFile);
  } catch (e) { /* ignore */ }
}

// --- Основные функции ---

/**
 * Читает GitHub токен из окружения или .env.
 */
function getGithubToken() {
  if (process.env.GITHUB_TOKEN) return process.env.GITHUB_TOKEN;
  return null;
}

/**
 * Сохраняет SHA текущего состояния (вызывается после первой установки).
 */
async function initializeUpdateSha() {
  const shaFile = getUpdateShaFile();
  if (fs.existsSync(shaFile)) return; // Уже инициализирован

  const token = getGithubToken();
  const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/commits/${GITHUB_BRANCH}`;
  const data = await httpsGetJson(url, token);

  if (data && data.sha) {
    fs.mkdirSync(path.dirname(shaFile), { recursive: true });
    fs.writeFileSync(shaFile, data.sha, 'utf8');
    log('Initialized update SHA', { sha: data.sha.substring(0, 8) });
  } else {
    // Нет сети — пишем заглушку, при следующем запуске скачает всё
    fs.mkdirSync(path.dirname(shaFile), { recursive: true });
    fs.writeFileSync(shaFile, 'unknown', 'utf8');
    log('No network — stored placeholder SHA');
  }
}

/**
 * Проверяет наличие обновлений Python-кода и применяет их.
 *
 * @param {function} onProgress — (step, percent, message) callback
 * @returns {Promise<{updated: boolean, changedFiles: string[], requirementsChanged: boolean, error?: string}>}
 */
async function checkForPythonUpdate(onProgress) {
  const report = (pct, msg) => {
    if (onProgress) onProgress('update', pct, msg);
    log(msg);
  };

  const result = {
    updated: false,
    fromSha: '',
    toSha: '',
    changedFiles: [],
    requirementsChanged: false,
  };

  // Lock
  if (!acquireLock()) {
    log('Another instance is updating, skipping');
    return result;
  }

  try {
    const shaFile = getUpdateShaFile();
    const token = getGithubToken();

    // 1. Читаем локальный SHA
    let localSha = '';
    if (fs.existsSync(shaFile)) {
      localSha = fs.readFileSync(shaFile, 'utf8').trim();
    }
    if (!localSha) {
      // Первый запуск — инициализируем и выходим
      await initializeUpdateSha();
      return result;
    }

    report(10, 'Проверка обновлений...');

    // 2. Получаем последний коммит на main
    const commitUrl = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/commits/${GITHUB_BRANCH}`;
    const commitData = await httpsGetJson(commitUrl, token);

    if (!commitData || !commitData.sha) {
      report(100, 'Нет связи с GitHub — пропускаем проверку обновлений');
      result.error = 'network';
      return result;
    }

    const remoteSha = commitData.sha;
    result.fromSha = localSha;
    result.toSha = remoteSha;

    // 3. Сравниваем
    if (remoteSha === localSha) {
      report(100, 'Обновлений нет');
      return result;
    }

    report(20, 'Найдены обновления, загрузка списка изменений...');

    // 4. Получаем diff
    let compareData;
    if (localSha === 'unknown') {
      // Первый раз или SHA потерян — скачиваем всё дерево src/
      compareData = await getFullTree(remoteSha, token);
    } else {
      const compareUrl = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/compare/${localSha}...${remoteSha}`;
      compareData = await httpsGetJson(compareUrl, token);
    }

    if (!compareData) {
      report(100, 'Не удалось получить список изменений — пропускаем');
      result.error = 'compare_failed';
      return result;
    }

    // 5. Фильтруем файлы
    const files = (compareData.files || []).filter(f => isWatchedFile(f.filename));

    if (files.length === 0) {
      // Изменения есть, но не в src/ и не в requirements.txt
      fs.writeFileSync(shaFile, remoteSha, 'utf8');
      report(100, 'Обновлений для backend нет');
      return result;
    }

    report(30, `Обновление ${files.length} файл(ов)...`);

    // 6. Применяем изменения
    let downloaded = 0;
    let errors = 0;

    for (const file of files) {
      const targetPath = getTargetPath(file.filename);
      const pct = 30 + Math.round((downloaded / files.length) * 60);

      try {
        if (file.status === 'removed') {
          // Удаляем файл
          if (fs.existsSync(targetPath)) {
            fs.unlinkSync(targetPath);
            log(`Deleted: ${file.filename}`);
          }
        } else if (file.status === 'renamed') {
          // Удаляем старый, скачиваем новый
          if (file.previous_filename) {
            const oldPath = getTargetPath(file.previous_filename);
            if (fs.existsSync(oldPath)) fs.unlinkSync(oldPath);
          }
          report(pct, `Обновление: ${path.basename(file.filename)}`);
          await downloadRawFile(remoteSha, file.filename, targetPath, token);
          log(`Renamed/downloaded: ${file.filename}`);
        } else {
          // added / modified
          report(pct, `Обновление: ${path.basename(file.filename)}`);
          await downloadRawFile(remoteSha, file.filename, targetPath, token);
          log(`Updated: ${file.filename}`);
        }

        result.changedFiles.push(file.filename);

        if (file.filename === 'requirements.txt') {
          result.requirementsChanged = true;
        }
      } catch (err) {
        log(`Error updating ${file.filename}`, { error: err.message });
        errors++;
      }

      downloaded++;
    }

    // 7. Сохраняем новый SHA
    if (errors === 0) {
      fs.writeFileSync(shaFile, remoteSha, 'utf8');
      result.updated = true;
      report(100, `Обновлено ${result.changedFiles.length} файл(ов)`);
    } else {
      // Частичное обновление — сохраняем SHA только если хотя бы что-то обновилось
      if (result.changedFiles.length > 0) {
        fs.writeFileSync(shaFile, remoteSha, 'utf8');
        result.updated = true;
        report(100, `Обновлено ${result.changedFiles.length} из ${files.length} файл(ов) (${errors} ошибок)`);
      } else {
        report(100, `Ошибка обновления (${errors} файлов не удалось скачать)`);
        result.error = 'download_failed';
      }
    }

    return result;
  } finally {
    releaseLock();
  }
}

/**
 * Получает полное дерево src/ для случая когда localSha = 'unknown'.
 * Возвращает объект в формате compare API: { files: [...] }
 */
async function getFullTree(sha, token) {
  const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/git/trees/${sha}?recursive=1`;
  const data = await httpsGetJson(url, token);
  if (!data || !data.tree) return null;

  const files = data.tree
    .filter(item => item.type === 'blob' && isWatchedFile(item.path))
    .map(item => ({
      filename: item.path,
      status: 'modified', // Treat all as modified — will overwrite
    }));

  return { files };
}

/**
 * Устанавливает обновлённые Python-зависимости (pip install -r requirements.txt).
 * Повторяет логику dependency-manager: если есть GPU, финализирует PyTorch CUDA.
 *
 * @param {function} onProgress — (step, percent, message) callback
 */
async function installUpdatedRequirements(onProgress) {
  const report = (pct, msg) => {
    if (onProgress) onProgress('pip-update', pct, msg);
    log(msg);
  };

  const pythonExe = depManager.getPythonExe();
  const reqPath = depManager.getRequirementsPath();

  if (!reqPath || !fs.existsSync(reqPath)) {
    report(100, 'requirements.txt не найден — пропускаем');
    return;
  }

  if (!fs.existsSync(pythonExe)) {
    report(100, 'Python не установлен — пропускаем pip install');
    return;
  }

  report(10, 'Обновление Python-зависимостей...');

  // pip install -r requirements.txt
  await runProcess(pythonExe, [
    '-m', 'pip', 'install',
    '-r', reqPath,
    '--no-cache-dir',
    '--no-warn-script-location',
  ], {
    cwd: depManager.getPythonDir(),
    env: { ...process.env, PYTHONUTF8: '1' },
    timeout: 600000,
  }, (line) => {
    const trimmed = line.trim();
    if (trimmed) report(50, trimmed);
  });

  // Если есть NVIDIA GPU — принудительно восстанавливаем PyTorch CUDA
  // (requirements.txt может перезаписать torch на CPU-версию из PyPI)
  const torchVariantFile = path.join(depManager.getBaseDir(), '.torch-variant');
  if (fs.existsSync(torchVariantFile)) {
    const variant = fs.readFileSync(torchVariantFile, 'utf8').trim();
    if (variant === 'cu124') {
      report(80, 'Восстановление PyTorch CUDA...');
      const TORCH_INDEX_CUDA = 'https://download.pytorch.org/whl/cu124';
      await runProcess(pythonExe, [
        '-m', 'pip', 'install',
        'torch', 'torchaudio',
        '--index-url', TORCH_INDEX_CUDA,
        '--force-reinstall',
        '--no-deps',
        '--no-cache-dir',
        '--no-warn-script-location',
      ], {
        cwd: depManager.getPythonDir(),
        env: { ...process.env, PYTHONUTF8: '1' },
        timeout: 900000,
      }, (line) => {
        const trimmed = line.trim();
        if (trimmed) report(90, trimmed);
      });
    }
  }

  report(100, 'Python-зависимости обновлены');
}

/** Запускает процесс и возвращает Promise (копия из dependency-manager). */
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
      if (code !== 0) return reject(new Error(`Process exited with code ${code}: ${stderr.slice(-500)}`));
      resolve();
    });
  });
}

module.exports = {
  checkForPythonUpdate,
  installUpdatedRequirements,
  initializeUpdateSha,
};
