#!/bin/bash
# Скрипт установки и настройки Voice Cloning (Coqui XTTS v2)
# Требуется Python 3.10+

set -e

echo "🎤 Настройка Voice Cloning для AI Dubbing Studio"
echo "================================================"
echo ""

# Проверяем версию Python
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

echo "📋 Текущая версия Python: $PYTHON_VERSION"

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]); then
    echo "⚠️  Требуется Python 3.10+, текущая версия: $PYTHON_VERSION"
    echo ""
    echo "💡 Установка Python 3.11 через Homebrew..."
    
    if ! command -v brew &> /dev/null; then
        echo "❌ Homebrew не установлен. Установите: https://brew.sh"
        exit 1
    fi
    
    brew install python@3.11
    
    echo ""
    echo "✅ Python 3.11 установлен"
    echo "📝 Создание виртуального окружения..."
    
    python3.11 -m venv venv_tts
    source venv_tts/bin/activate
    
    echo "✅ Виртуальное окружение создано"
    PYTHON_CMD=python3.11
else
    echo "✅ Версия Python подходит"
    PYTHON_CMD=python3
fi

echo ""
echo "📦 Установка зависимостей..."

# Устанавливаем зависимости
$PYTHON_CMD -m pip install --upgrade pip setuptools wheel

# Устанавливаем PyTorch и Torchaudio (требуются для TTS)
echo "📦 Установка PyTorch и Torchaudio..."
$PYTHON_CMD -m pip install torch torchaudio

# Устанавливаем numpy и pandas (совместимые версии)
echo "📦 Установка numpy и pandas..."
$PYTHON_CMD -m pip install "numpy>=2.0.2,<2.1.0" "pandas>=2.2.3,<2.3.0"

# Устанавливаем TTS с поддержкой codec (требуется для PyTorch 2.9+)
echo "📦 Установка coqui-tts[codec] и pydub..."
$PYTHON_CMD -m pip install "coqui-tts[codec]" pydub

echo ""
echo "✅ Все зависимости установлены!"
echo ""
echo "🧪 Проверка установки..."

$PYTHON_CMD -c "
import sys
errors = []

# Проверка PyTorch
try:
    import torch
    print('✅ PyTorch установлен:', torch.__version__)
except ImportError as e:
    errors.append(f'PyTorch: {e}')

# Проверка Torchaudio
try:
    import torchaudio
    print('✅ Torchaudio установлен:', torchaudio.__version__)
except ImportError as e:
    errors.append(f'Torchaudio: {e}')

# Проверка TTS
try:
    from TTS.api import TTS
    print('✅ TTS успешно импортирован')
except ImportError as e:
    errors.append(f'TTS: {e}')

# Проверка pydub
try:
    import pydub
    print('✅ pydub успешно импортирован')
except ImportError as e:
    errors.append(f'pydub: {e}')

if errors:
    print('')
    print('❌ Ошибки установки:')
    for error in errors:
        print(f'   - {error}')
    sys.exit(1)
else:
    print('')
    print('🎉 Voice Cloning готов к использованию!')
"

echo ""
echo "================================================"
echo "✅ Настройка завершена успешно!"
echo ""
if [ -d "venv_tts" ]; then
    echo "💡 Для использования активируйте виртуальное окружение:"
    echo "   source venv_tts/bin/activate"
    echo ""
fi
echo "📚 Модуль voice_cloner.py готов к использованию"
