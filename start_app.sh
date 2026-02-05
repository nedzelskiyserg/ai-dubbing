#!/bin/bash

# =============================================================================
# AI Dubbing Studio — запуск для локальной разработки на Mac
# Запускает: Ollama → API сервер → React → Electron
# =============================================================================

set -e  # Выходим при ошибке

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Сохраняем корневую директорию скрипта
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}🚀 AI Dubbing Studio — Профессиональный дубляж${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# -----------------------------------------------------------------------------
# 1. Проверка системных зависимостей
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[1/6] Проверка системных зависимостей...${NC}"

# Node.js
if ! command -v node &> /dev/null; then
    echo -e "${RED}❌ Node.js не установлен. Установите: brew install node${NC}"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} Node.js $(node --version)"

# FFmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo -e "${RED}❌ FFmpeg не установлен. Установите: brew install ffmpeg${NC}"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} FFmpeg установлен"

# Python 3.10+
if [ -d "venv_tts" ]; then
    PYTHON_CMD="$SCRIPT_DIR/venv_tts/bin/python"
else
    PYTHON_CMD="python3"
fi

if ! $PYTHON_CMD --version &> /dev/null; then
    echo -e "${RED}❌ Python не найден${NC}"
    exit 1
fi
PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | cut -d' ' -f2)
echo -e "  ${GREEN}✓${NC} Python $PYTHON_VERSION"

# -----------------------------------------------------------------------------
# 2. Проверка и запуск Ollama (для умного перевода)
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[2/6] Проверка Ollama (умный перевод)...${NC}"

OLLAMA_REQUIRED=true

if ! command -v ollama &> /dev/null; then
    echo -e "  ${YELLOW}⚠️  Ollama не установлен${NC}"
    echo -e "  ${YELLOW}   Установите: brew install ollama${NC}"
    echo -e "  ${YELLOW}   Без Ollama будет использоваться обычный перевод (API)${NC}"
    OLLAMA_REQUIRED=false
else
    # Проверяем, запущен ли Ollama
    if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo -e "  ${YELLOW}⏳ Запуск Ollama...${NC}"
        ollama serve > /dev/null 2>&1 &
        OLLAMA_PID=$!
        sleep 3

        if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
            echo -e "  ${GREEN}✓${NC} Ollama запущен"
        else
            echo -e "  ${YELLOW}⚠️  Не удалось запустить Ollama, используем API перевод${NC}"
            OLLAMA_REQUIRED=false
        fi
    else
        echo -e "  ${GREEN}✓${NC} Ollama уже запущен"
    fi

    # Проверяем наличие модели qwen2.5:7b
    if [ "$OLLAMA_REQUIRED" = true ]; then
        if ! ollama list 2>/dev/null | grep -q "qwen2.5:7b"; then
            echo -e "  ${YELLOW}⏳ Загрузка модели qwen2.5:7b (4.7 GB)...${NC}"
            echo -e "  ${YELLOW}   Это нужно только один раз${NC}"
            ollama pull qwen2.5:7b
        fi
        echo -e "  ${GREEN}✓${NC} Модель qwen2.5:7b готова"
    fi
fi

# -----------------------------------------------------------------------------
# 3. Активация Python окружения и проверка зависимостей
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[3/6] Проверка Python окружения...${NC}"

if [ -d "venv_tts" ]; then
    source venv_tts/bin/activate
    echo -e "  ${GREEN}✓${NC} venv_tts активирован"

    # Проверяем критические зависимости
    if ! python -c "import flask, TTS, whisperx, noisereduce" 2>/dev/null; then
        echo -e "  ${YELLOW}⏳ Установка зависимостей...${NC}"
        pip install -r requirements.txt -q
        pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu -q
    fi
    echo -e "  ${GREEN}✓${NC} Python зависимости установлены"
else
    echo -e "  ${RED}❌ venv_tts не найден${NC}"
    echo -e "  ${YELLOW}   Создайте: python3.11 -m venv venv_tts${NC}"
    echo -e "  ${YELLOW}   Затем: source venv_tts/bin/activate && pip install -r requirements.txt${NC}"
    exit 1
fi

# -----------------------------------------------------------------------------
# 4. Проверка и установка Node.js зависимостей
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[4/6] Проверка Node.js зависимостей...${NC}"

if [ ! -d "frontend/node_modules" ]; then
    echo -e "  ${YELLOW}⏳ Установка npm зависимостей...${NC}"
    cd "$SCRIPT_DIR/frontend" && npm install --silent
    cd "$SCRIPT_DIR"
fi
echo -e "  ${GREEN}✓${NC} Node.js зависимости установлены"

# -----------------------------------------------------------------------------
# 5. Очистка старых процессов и запуск серверов
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[5/6] Запуск серверов...${NC}"

# Очищаем старые процессы
pkill -f "api_server.py" 2>/dev/null || true
pkill -f "react-scripts" 2>/dev/null || true
lsof -ti:5001 | xargs kill -9 2>/dev/null || true
lsof -ti:3000 | xargs kill -9 2>/dev/null || true
sleep 1

# Запускаем API сервер
echo -e "  ${YELLOW}⏳ Запуск API сервера...${NC}"
$PYTHON_CMD src/api_server.py > /tmp/ai_dubbing_api.log 2>&1 &
API_PID=$!

# Ждём запуска API
for i in {1..10}; do
    if curl -s http://localhost:5001/api/health > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

if curl -s http://localhost:5001/api/health > /dev/null 2>&1; then
    echo -e "  ${GREEN}✓${NC} API сервер запущен (PID: $API_PID)"
else
    echo -e "  ${RED}❌ Ошибка запуска API сервера${NC}"
    echo -e "  ${YELLOW}   Лог: cat /tmp/ai_dubbing_api.log${NC}"
    exit 1
fi

# Запускаем React
echo -e "  ${YELLOW}⏳ Запуск React...${NC}"
cd "$SCRIPT_DIR/frontend"
BROWSER=none npm start > /tmp/ai_dubbing_react.log 2>&1 &
REACT_PID=$!
cd "$SCRIPT_DIR"

# Ждём запуска React
for i in {1..30}; do
    if curl -s http://localhost:3000 > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

if curl -s http://localhost:3000 > /dev/null 2>&1; then
    echo -e "  ${GREEN}✓${NC} React запущен (PID: $REACT_PID)"
else
    echo -e "  ${RED}❌ Ошибка запуска React${NC}"
    echo -e "  ${YELLOW}   Лог: cat /tmp/ai_dubbing_react.log${NC}"
    exit 1
fi

# -----------------------------------------------------------------------------
# 6. Запуск Electron
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[6/6] Запуск приложения...${NC}"

cd "$SCRIPT_DIR/frontend"
ELECTRON_IS_DEV=1 npm run electron-dev &
ELECTRON_PID=$!
cd "$SCRIPT_DIR"

sleep 2
echo -e "  ${GREEN}✓${NC} Electron запущен (PID: $ELECTRON_PID)"

# -----------------------------------------------------------------------------
# Финальный вывод
# -----------------------------------------------------------------------------
echo -e "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✅ AI Dubbing Studio запущен!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e ""
echo -e "  ${BLUE}🌐 API:${NC}      http://localhost:5001"
echo -e "  ${BLUE}⚛️  React:${NC}    http://localhost:3000"
if [ "$OLLAMA_REQUIRED" = true ]; then
echo -e "  ${BLUE}🤖 Ollama:${NC}   http://localhost:11434 (qwen2.5:7b)"
fi
echo -e ""
echo -e "  ${YELLOW}Для выхода нажмите Ctrl+C${NC}"
echo -e ""

# Обработчик завершения
cleanup() {
    echo -e "\n${YELLOW}⏹️  Остановка сервисов...${NC}"
    kill $API_PID 2>/dev/null || true
    kill $REACT_PID 2>/dev/null || true
    kill $ELECTRON_PID 2>/dev/null || true
    if [ -n "$OLLAMA_PID" ]; then
        kill $OLLAMA_PID 2>/dev/null || true
    fi
    echo -e "${GREEN}✅ Готово${NC}"
    exit 0
}

trap cleanup EXIT INT TERM

# Ждём завершения Electron
wait $ELECTRON_PID 2>/dev/null
