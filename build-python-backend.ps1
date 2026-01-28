# Скрипт для упаковки Python backend для Electron
# Используется в GitHub Actions
# ОБЯЗАТЕЛЬНЫЙ КОМПОНЕНТ - приложение не будет работать без него!

$ErrorActionPreference = "Stop"  # Останавливаем выполнение при любой ошибке

Write-Host "🔨 Начинаем упаковку Python backend (обязательный компонент)..."

# Ищем Python: на Windows часто в PATH только "py" (Python Launcher), не "python"
Write-Host "🐍 Проверка Python..."
$PythonCmd = $null
foreach ($c in @("py -3", "py", "python", "python3")) {
    cmd /c "$c --version" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $PythonCmd = $c
        $ver = cmd /c "$c --version" 2>&1
        Write-Host "✅ Python найден: $ver (команда: $c)"
        break
    }
}
if (-not $PythonCmd) {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Python не найден!"
    Write-Host "Установите Python 3.10+ с опцией 'Add Python to PATH' или запустите через 'py' (Python Launcher)."
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

# Проверяем, что Flask доступен (иначе exe выдаст ModuleNotFoundError: No module named 'flask')
Write-Host "🔍 Проверка Flask..."
try {
    $null = python -c "import flask; import flask_cors; print('OK')"
    Write-Host "✅ Flask доступен"
} catch {
    Write-Host "❌ КРИТИЧЕСКАЯ ОШИБКА: Flask не найден в venv (pip install flask flask-cors)"
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

# Явно подключаем Flask и зависимости (иначе в exe: ModuleNotFoundError: No module named 'flask')
$pyinstallerCmd += " --hidden-import=flask --hidden-import=flask_cors --hidden-import=werkzeug --hidden-import=werkzeug.serving --hidden-import=jinja2"
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
