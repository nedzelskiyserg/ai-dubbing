# Скрипт для упаковки Python backend для Electron
# Используется в GitHub Actions

Write-Host "🔨 Начинаем упаковку Python backend..."

# Создаем директорию для упакованного backend
New-Item -ItemType Directory -Force -Path "python-backend-dist" | Out-Null

# Создаем venv для сборки
Write-Host "📦 Создаем Python venv..."
python -m venv venv_build
.\venv_build\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

# Собираем Python backend с PyInstaller
Write-Host "🔨 Собираем Python backend с PyInstaller..."
$pyinstallerCmd = "pyinstaller --clean --noconfirm --onedir --name `"api-server`" --distpath python-backend-dist"

# Добавляем FFmpeg
if (Test-Path "ffmpeg\ffmpeg.exe") {
    $pyinstallerCmd += " --add-binary `"ffmpeg\ffmpeg.exe;.`""
    $dllFiles = Get-ChildItem -Path ffmpeg -Filter *.dll
    foreach ($dll in $dllFiles) {
        $pyinstallerCmd += " --add-binary `"ffmpeg\$($dll.Name);.`""
    }
}

# Добавляем остальные параметры
$pyinstallerCmd += " --collect-all flask --collect-all flask_cors --collect-all faster_whisper --collect-all pyannote --hidden-import=whisperx --hidden-import=torch --hidden-import=torchaudio --hidden-import=coqui --hidden-import=moviepy src/api_server.py"

Invoke-Expression $pyinstallerCmd

# Копируем упакованный backend в нужное место
Write-Host "📋 Копируем упакованный backend..."
if (Test-Path "python-backend-dist\api-server") {
    Copy-Item -Path "python-backend-dist\api-server" -Destination "frontend\build\python-backend" -Recurse -Force
    Write-Host "✅ Python backend упакован и скопирован"
} else {
    Write-Host "❌ Ошибка: Python backend не был собран"
    exit 1
}

Write-Host "✅ Упаковка Python backend завершена"
