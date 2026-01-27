#!/bin/bash

# Скрипт для запуска в полноэкранном режиме как нативное приложение
# Использует Electron для создания нативного окна

echo "🚀 Запуск AI Dubbing Studio в полноэкранном режиме..."

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
    cd frontend
    npm install
    cd ..
fi

# Очищаем порты если заняты
lsof -ti:5001 | xargs kill -9 2>/dev/null
lsof -ti:3000 | xargs kill -9 2>/dev/null
pkill -f "react-scripts" 2>/dev/null
sleep 1

# Запускаем API сервер в фоне (логи выводятся в консоль)
echo "🔧 Запуск API сервера на порту 5001..."
echo "📝 Логи API сервера будут выводиться в эту консоль"
# Используем python3 из venv если доступен
# НЕ перенаправляем stdout/stderr, чтобы логи были видны в консоли
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

# Запускаем React приложение
echo "⚛️  Запуск React приложения..."
cd frontend
BROWSER=none npm start > /tmp/react_app.log 2>&1 &
REACT_PID=$!

# Ждем запуска React и проверяем готовность
echo "⏳ Ожидание запуска React приложения..."
for i in {1..15}; do
    if curl -s http://localhost:3000 > /dev/null 2>&1; then
        echo "✅ React приложение готово"
        break
    fi
    sleep 1
done

# Открываем в полноэкранном режиме через Chrome/Edge в kiosk mode
echo "🖥️  Открытие в полноэкранном режиме..."

# Пробуем разные браузеры (kiosk mode - лучший вариант для полноэкранного режима)
ARC_PATH=""
CHROME_PATH=""
EDGE_PATH=""
CHROMIUM_PATH=""

# Ищем Arc (приоритет, так как пользователь указал его)
if [ -d "/Applications/Arc.app" ]; then
    ARC_PATH="/Applications/Arc.app/Contents/MacOS/Arc"
fi

# Ищем Chrome
if [ -d "/Applications/Google Chrome.app" ]; then
    CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
elif [ -d "/Applications/Chromium.app" ]; then
    CHROMIUM_PATH="/Applications/Chromium.app/Contents/MacOS/Chromium"
fi

# Ищем Edge
if [ -d "/Applications/Microsoft Edge.app" ]; then
    EDGE_PATH="/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
fi

# Открываем в найденном браузере (Arc имеет приоритет)
if [ -n "$ARC_PATH" ] && [ -f "$ARC_PATH" ]; then
    # Arc поддерживает kiosk mode
    "$ARC_PATH" --kiosk --app=http://localhost:3000 2>/dev/null &
    echo "✅ Открыто в Arc (kiosk mode - полноэкранный режим)"
    sleep 1
elif [ -n "$CHROME_PATH" ] && [ -f "$CHROME_PATH" ]; then
    "$CHROME_PATH" --kiosk --app=http://localhost:3000 2>/dev/null &
    echo "✅ Открыто в Chrome (kiosk mode - полноэкранный режим)"
    sleep 1
elif [ -n "$EDGE_PATH" ] && [ -f "$EDGE_PATH" ]; then
    "$EDGE_PATH" --kiosk --app=http://localhost:3000 2>/dev/null &
    echo "✅ Открыто в Edge (kiosk mode - полноэкранный режим)"
    sleep 1
elif [ -n "$CHROMIUM_PATH" ] && [ -f "$CHROMIUM_PATH" ]; then
    "$CHROMIUM_PATH" --kiosk --app=http://localhost:3000 2>/dev/null &
    echo "✅ Открыто в Chromium (kiosk mode - полноэкранный режим)"
    sleep 1
else
    # Fallback: используем Safari в полноэкранном режиме через AppleScript
    echo "⚠️  Chrome/Edge не найден, используем Safari"
    echo "💡 Совет: Установите Chrome для лучшего полноэкранного режима: https://www.google.com/chrome/"
    
    # Открываем через команду open, затем переводим в полноэкранный режим
    open -a Safari "http://localhost:3000"
    sleep 3
    
    # Переводим Safari в полноэкранный режим
    osascript <<'EOF'
tell application "Safari"
    activate
    delay 0.5
end tell
tell application "System Events"
    tell process "Safari"
        -- Скрываем панель инструментов
        keystroke "|" using {command down, option down}
        delay 0.5
        -- Переводим в полноэкранный режим
        keystroke "f" using {command down, control down}
    end tell
end tell
EOF
    echo "✅ Открыто в Safari (полноэкранный режим)"
fi

echo "✅ Приложение запущено в полноэкранном режиме"
echo "📝 Для выхода нажмите Ctrl+C"

# Ждем завершения React процесса
wait $REACT_PID

# При выходе убиваем все процессы
trap "kill $API_PID $REACT_PID 2>/dev/null; exit" EXIT INT TERM
