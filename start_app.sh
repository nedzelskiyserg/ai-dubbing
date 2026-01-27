#!/bin/bash

# Скрипт для запуска как нативное приложение (Electron)

# Сохраняем корневую директорию скрипта
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo "🚀 Запуск AI Dubbing Studio как нативное приложение..."

# Проверяем, установлен ли Node.js
if ! command -v node &> /dev/null; then
    echo "❌ Node.js не установлен. Установите Node.js: https://nodejs.org/"
    exit 1
fi

# Активируем виртуальное окружение если есть
if [ -d ".venv" ]; then
    source .venv/bin/activate
    echo "✅ Виртуальное окружение активировано"
    
    # Проверяем критические зависимости
    echo "📦 Проверка зависимостей..."
    if ! .venv/bin/python3 -c "import flask, flask_cors, pydub" 2>/dev/null; then
        echo "⚠️  Устанавливаю недостающие зависимости..."
        pip3 install flask flask-cors pydub requests deep-translator > /dev/null 2>&1
    fi
fi

# Проверяем, установлены ли зависимости
if [ ! -d "frontend/node_modules" ]; then
    echo "📦 Установка зависимостей React..."
    cd "$SCRIPT_DIR/frontend" || exit 1
    npm install
    cd "$SCRIPT_DIR" || exit 1
fi

# Проверяем, установлен ли Electron
if [ ! -d "frontend/node_modules/electron" ]; then
    echo "📦 Установка Electron..."
    cd "$SCRIPT_DIR/frontend" || exit 1
    npm install electron electron-is-dev
    cd "$SCRIPT_DIR" || exit 1
fi

# Очищаем порты если заняты
lsof -ti:5001 | xargs kill -9 2>/dev/null
lsof -ti:3000 | xargs kill -9 2>/dev/null
pkill -f "react-scripts" 2>/dev/null
pkill -f "electron" 2>/dev/null
sleep 1

# Запускаем API сервер в фоне (логи выводятся в консоль)
echo "🔧 Запуск API сервера на порту 5001..."
if [ -d ".venv" ]; then
    .venv/bin/python3 src/api_server.py &
else
    python3 src/api_server.py &
fi
API_PID=$!

# Ждем немного, чтобы API сервер запустился
sleep 3

# Проверяем, что API сервер запустился
if ! ps -p $API_PID > /dev/null; then
    echo "❌ Ошибка запуска API сервера. Проверьте вывод выше."
    exit 1
fi

echo "✅ API сервер запущен (PID: $API_PID)"

# Запускаем React приложение в фоне
echo "⚛️  Запуск React приложения..."
cd "$SCRIPT_DIR/frontend" || exit 1
BROWSER=none npm start > /tmp/react_app.log 2>&1 &
REACT_PID=$!
cd "$SCRIPT_DIR" || exit 1

# Ждем запуска React
echo "⏳ Ожидание запуска React приложения..."
for i in {1..20}; do
    if curl -s http://localhost:3000 > /dev/null 2>&1; then
        echo "✅ React приложение готово"
        break
    fi
    sleep 1
done

# Запускаем Electron приложение
echo "🖥️  Запуск нативного приложения (Electron)..."
cd "$SCRIPT_DIR/frontend" || exit 1
ELECTRON_IS_DEV=1 npm run electron-dev &
ELECTRON_PID=$!
cd "$SCRIPT_DIR" || exit 1

echo "✅ Приложение запущено в нативном окне"
echo "📝 Для выхода закройте окно приложения или нажмите Ctrl+C"

# Ждем завершения Electron процесса
wait $ELECTRON_PID

# При выходе убиваем все процессы
trap "kill $API_PID $REACT_PID $ELECTRON_PID 2>/dev/null; exit" EXIT INT TERM
