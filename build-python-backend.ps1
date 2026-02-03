# Скрипт для упаковки Python backend для Electron
# Используется в GitHub Actions
# ОБЯЗАТЕЛЬНЫЙ КОМПОНЕНТ - приложение не будет работать без него!

$ErrorActionPreference = "Stop"  # Останавливаем выполнение при любой ошибке

Write-Host "🔨 Начинаем упаковку Python backend (обязательный компонент)..."

# Ищем Python 3.10–3.12 (whisperx требует <3.14,>=3.10). В CI (GitHub Actions) первым идёт "python" из setup-python.
Write-Host "🐍 Проверка Python (нужен 3.10, 3.11 или 3.12 — не 3.14!)..."
$PythonCmd = $null
foreach ($c in @("python", "py -3.12", "py -3.11", "py -3.10", "py -3", "python3")) {
    $verOut = cmd /c "$c --version" 2>&1
    if ($LASTEXITCODE -eq 0 -and $verOut) {
        # Проверяем, что не 3.14 (whisperx не поддерживает)
        if ($verOut -match "3\.14\.\d+") {
            Write-Host "   Пропуск $c — $verOut (whisperx не поддерживает 3.14)"
            continue
        }
        if ($verOut -match "3\.(10|11|12)\.\d+") {
            $PythonCmd = $c
            Write-Host "✅ Python найден: $verOut (команда: $c)"
            break
        }
        Write-Host "   Пропуск $c — $verOut (нужен 3.10–3.12)"
    }
}
if (-not $PythonCmd) {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Нужен Python 3.10, 3.11 или 3.12 (не 3.14!)."
    Write-Host "В CI используйте actions/setup-python с python-version: '3.10' и вызывайте скрипт без py -3."
    exit 1
}

# Проверяем наличие requirements.txt
if (-not (Test-Path "requirements.txt")) {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: requirements.txt не найден!"
    exit 1
}

# Создаем директорию для упакованного backend
New-Item -ItemType Directory -Force -Path "python-backend-dist" | Out-Null

# Создаем venv для сборки
Write-Host "📦 Создаем Python venv..."
try {
    Invoke-Expression "$PythonCmd -m venv venv_build"
    if (-not (Test-Path "venv_build\Scripts\Activate.ps1")) {
        Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось создать venv"
        exit 1
    }
    
    .\venv_build\Scripts\Activate.ps1
    # На Windows pip читает requirements.txt в cp1252 — при UTF-8 в файле получаем UnicodeDecodeError. Включаем UTF-8.
    $env:PYTHONUTF8 = "1"
    pip install --upgrade pip
    Write-Host "✅ Venv создан и активирован"
} catch {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка при создании venv"
    Write-Host "Ошибка: $_"
    exit 1
}

# Устанавливаем зависимости (PYTHONUTF8=1 уже задан выше — читаем requirements.txt в UTF-8)
Write-Host "📦 Установка зависимостей..."
$pipResult = pip install -r requirements.txt 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: pip install завершился с кодом $LASTEXITCODE"
    Write-Host $pipResult
    exit 1
}
if ($pipResult -match "requires a different Python|not in ") {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Одна из зависимостей не поддерживает текущую версию Python"
    Write-Host $pipResult
    exit 1
}
pip install pyinstaller 2>&1 | Out-Null
Write-Host "✅ Зависимости установлены"

# Проверяем Flask по коду выхода (PowerShell try/catch не ловит exit code дочернего процесса)
Write-Host "🔍 Проверка Flask..."
& python -c "import flask; import flask_cors; print('OK')" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Flask не найден в venv. Установите Python 3.10–3.12 (не 3.14) и перезапустите сборку."
    exit 1
}
Write-Host "✅ Flask доступен"

# Проверяем наличие FFmpeg (обязателен для работы)
Write-Host "🎬 Проверка FFmpeg..."
if (-not (Test-Path "ffmpeg\ffmpeg.exe")) {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: FFmpeg не найден!"
    Write-Host "FFmpeg должен быть скачан на предыдущем шаге"
    exit 1
}
Write-Host "✅ FFmpeg найден"

# Собираем Python backend с PyInstaller
Write-Host "🔨 Собираем Python backend с PyInstaller..."
$pyinstallerCmd = "pyinstaller --clean --noconfirm --onedir --name `"api-server`" --distpath python-backend-dist"

# Добавляем FFmpeg (обязательно)
$pyinstallerCmd += " --add-binary `"ffmpeg\ffmpeg.exe;.`""
$dllFiles = Get-ChildItem -Path ffmpeg -Filter *.dll
foreach ($dll in $dllFiles) {
    $pyinstallerCmd += " --add-binary `"ffmpeg\$($dll.Name);.`""
}

# Явно подключаем Flask и зависимости
$pyinstallerCmd += " --hidden-import=flask --hidden-import=flask_cors --hidden-import=werkzeug --hidden-import=werkzeug.serving --hidden-import=jinja2"

# Оптимизация размера:
# 1. Убираем тяжелые --collect-all для torch, torchaudio, coqui (используем стандартные хуки PyInstaller)
# 2. Оставляем --collect-all для капризных библиотек (whisperx, faster_whisper, pyannote)
# 3. Используем --copy-metadata для библиотек, которым нужны версии/метаданные
$pyinstallerCmd += " --collect-all faster_whisper --collect-all pyannote --collect-all whisperx"
$pyinstallerCmd += " --hidden-import=whisperx.asr --hidden-import=whisperx.audio --hidden-import=whisperx.alignment --hidden-import=whisperx.diarize --hidden-import=whisperx.utils"
$pyinstallerCmd += " --hidden-import=pandas --hidden-import=scipy.signal --hidden-import=torch --hidden-import=torchaudio --hidden-import=coqui --hidden-import=moviepy"

# Копируем метаданные (важно для torch, tqdm, huggingface_hub)
$pyinstallerCmd += " --copy-metadata=torch --copy-metadata=tqdm --copy-metadata=regex --copy-metadata=requests --copy-metadata=packaging --copy-metadata=filelock --copy-metadata=numpy --copy-metadata=huggingface_hub --copy-metadata=safetensors"

# Добавляем tts_worker.py (запускается через subprocess)
$pyinstallerCmd += " --add-data `"src/core/tts_worker.py;core`""

# Исключаем ненужные модули для уменьшения размера
$pyinstallerCmd += " --exclude-module=tkinter --exclude-module=matplotlib --exclude-module=IPython --exclude-module=jupyter --exclude-module=notebook --exclude-module=pytest --exclude-module=unittest --exclude-module=pydoc --exclude-module=doctest"

$pyinstallerCmd += " src/api_server.py"

Write-Host "📝 Команда PyInstaller: $pyinstallerCmd"

try {
    Invoke-Expression $pyinstallerCmd
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: PyInstaller завершился с ошибкой (код: $LASTEXITCODE)"
        exit 1
    }
    Write-Host "✅ PyInstaller завершил сборку"
} catch {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка при выполнении PyInstaller"
    Write-Host "Ошибка: $_"
    exit 1
}

# Проверяем результат сборки
Write-Host "🔍 Проверка результата сборки..."
$backendDir = "python-backend-dist\api-server"
$backendExe = "$backendDir\api-server.exe"

Write-Host "Проверяем директорию: $backendDir"
if (-not (Test-Path $backendDir)) {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Директория сборки не найдена: $backendDir"
    Write-Host "Содержимое python-backend-dist:"
    if (Test-Path "python-backend-dist") {
        Get-ChildItem "python-backend-dist" | ForEach-Object { Write-Host "  - $($_.Name)" }
    }
    exit 1
}

Write-Host "Проверяем файл: $backendExe"
if (-not (Test-Path $backendExe)) {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Исполняемый файл не найден: $backendExe"
    Write-Host "Содержимое директории $backendDir :"
    Get-ChildItem $backendDir | Select-Object -First 20 | ForEach-Object { Write-Host "  - $($_.Name) ($($_.PSIsContainer ? 'DIR' : 'FILE'))" }
    exit 1
}

# Проверяем размер файла
$fileSize = (Get-Item $backendExe).Length
if ($fileSize -eq 0) {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Исполняемый файл пустой!"
    exit 1
}

Write-Host "✅ Исполняемый файл найден (размер: $([math]::Round($fileSize/1MB, 2)) MB)"

# Копируем упакованный backend в нужное место
Write-Host "📋 Копируем упакованный backend..."
try {
    $targetBaseDir = "frontend\build\python-backend"
    
    Write-Host "Создаем базовую директорию: $targetBaseDir"
    New-Item -ItemType Directory -Force -Path $targetBaseDir | Out-Null
    
    # Удаляем старую директорию, если существует
    if (Test-Path $targetBaseDir) {
        Write-Host "Очищаем старую директорию: $targetBaseDir"
        Remove-Item -Path "$targetBaseDir\*" -Recurse -Force -ErrorAction SilentlyContinue
    }
    
    Write-Host "Копируем из: $backendDir"
    Write-Host "Копируем в: $targetBaseDir"
    
    # Копируем всю директорию api-server в правильное место
    # Результат: frontend/build/python-backend/api-server/api-server.exe
    Copy-Item -Path $backendDir -Destination $targetBaseDir -Recurse -Force
    
    # Финальная проверка - файл должен быть в targetBaseDir/api-server/api-server.exe
    $finalExe = "$targetBaseDir\api-server\api-server.exe"
    Write-Host "Проверяем наличие: $finalExe"
    
    if (-not (Test-Path $finalExe)) {
        Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось скопировать Python backend"
        Write-Host "Ожидаемый файл: $finalExe"
        Write-Host "Содержимое $targetBaseDir :"
        if (Test-Path $targetBaseDir) {
            Get-ChildItem $targetBaseDir -Recurse | Select-Object -First 30 | ForEach-Object { 
                $type = if ($_.PSIsContainer) { "DIR" } else { "FILE" }
                Write-Host "  [$type] $($_.FullName)"
            }
        }
        exit 1
    }
    
    $finalSize = (Get-Item $finalExe).Length
    Write-Host "✅ Python backend успешно упакован и скопирован (размер: $([math]::Round($finalSize/1MB, 2)) MB)"
    Write-Host "Финальный путь: $finalExe"
} catch {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка при копировании backend"
    Write-Host "Ошибка: $_"
    exit 1
}

Write-Host "✅ Упаковка Python backend завершена успешно"
