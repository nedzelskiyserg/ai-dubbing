#!/bin/bash

# Скрипт для установки Google Chrome (опционально)

echo "🌐 Установка Google Chrome для полноэкранного режима..."
echo ""
echo "Chrome обеспечивает лучший полноэкранный режим (kiosk mode)"
echo "для AI Dubbing Studio."
echo ""

# Проверяем, установлен ли уже Chrome
if [ -d "/Applications/Google Chrome.app" ]; then
    echo "✅ Google Chrome уже установлен!"
    exit 0
fi

echo "📥 Скачивание Google Chrome..."
echo ""

# Скачиваем Chrome
CHROME_DMG="/tmp/googlechrome.dmg"
curl -L "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg" -o "$CHROME_DMG"

if [ ! -f "$CHROME_DMG" ]; then
    echo "❌ Ошибка скачивания Chrome"
    echo "Пожалуйста, установите вручную: https://www.google.com/chrome/"
    exit 1
fi

echo "📦 Установка Chrome..."
hdiutil attach "$CHROME_DMG" -quiet
sleep 2

# Копируем в Applications
cp -R "/Volumes/Google Chrome/Google Chrome.app" "/Applications/" 2>/dev/null

# Отключаем образ
hdiutil detach "/Volumes/Google Chrome" -quiet 2>/dev/null

# Удаляем DMG
rm "$CHROME_DMG"

if [ -d "/Applications/Google Chrome.app" ]; then
    echo "✅ Google Chrome успешно установлен!"
    echo ""
    echo "Теперь запустите: ./start_fullscreen.sh"
else
    echo "❌ Ошибка установки. Установите вручную: https://www.google.com/chrome/"
    exit 1
fi
