# Скрипт для упаковки Python backend для Electron
# Используется в GitHub Actions
# ОБЯЗАТЕЛЬНЫЙ КОМПОНЕНТ - приложение не будет работать без него!

$ErrorActionPreference = "Stop"  # Останавливаем выполнение при любой ошибке

Write-Host "🔨 Начинаем упаковку Python backend (обязательный компонент)..."

# Проверяем наличие Python
Write-Host "🐍 Проверка Python..."
try {
    $pythonVersion = python --version 2>&1
    Write-Host "✅ Python найден: $pythonVersion"
} catch {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Python не найден!"
    Write-Host "Python обязателен для сборки backend"
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
    python -m venv venv_build
    if (-not (Test-Path "venv_build\Scripts\Activate.ps1")) {
        Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось создать venv"
        exit 1
    }
    
    .\venv_build\Scripts\Activate.ps1
    pip install --upgrade pip
    Write-Host "✅ Venv создан и активирован"
} catch {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка при создании venv"
    Write-Host "Ошибка: $_"
    exit 1
}

# Устанавливаем зависимости
Write-Host "📦 Установка зависимостей..."
try {
    pip install -r requirements.txt
    pip install pyinstaller
    Write-Host "✅ Зависимости установлены"
} catch {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось установить зависимости"
    Write-Host "Ошибка: $_"
    exit 1
}

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

# Добавляем остальные параметры
$pyinstallerCmd += " --collect-all flask --collect-all flask_cors --collect-all faster_whisper --collect-all pyannote --hidden-import=whisperx --hidden-import=torch --hidden-import=torchaudio --hidden-import=coqui --hidden-import=moviepy src/api_server.py"

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

if (-not (Test-Path $backendDir)) {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Директория сборки не найдена: $backendDir"
    exit 1
}

if (-not (Test-Path $backendExe)) {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Исполняемый файл не найден: $backendExe"
    Write-Host "Содержимое директории:"
    Get-ChildItem $backendDir | ForEach-Object { Write-Host "  - $($_.Name)" }
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
    New-Item -ItemType Directory -Force -Path "frontend\build\python-backend" | Out-Null
    Copy-Item -Path $backendDir -Destination "frontend\build\python-backend" -Recurse -Force
    
    # Финальная проверка
    if (-not (Test-Path "frontend\build\python-backend\api-server\api-server.exe")) {
        Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось скопировать Python backend"
        exit 1
    }
    
    Write-Host "✅ Python backend успешно упакован и скопирован"
} catch {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Ошибка при копировании backend"
    Write-Host "Ошибка: $_"
    exit 1
}

Write-Host "✅ Упаковка Python backend завершена успешно"
