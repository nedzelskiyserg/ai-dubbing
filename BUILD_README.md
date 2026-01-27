# Инструкция по сборке AI Dubbing Studio

## Автоматическая сборка через GitHub Actions

При каждом push в ветку `main` автоматически запускается сборка Electron приложения для Windows.

### Результаты сборки

После успешной сборки артефакты будут доступны в разделе **Actions** GitHub:
- `AI-Dubbing-Studio-Windows` - установщик NSIS (.exe)
- `AI-Dubbing-Studio-Windows-Unpacked` - распакованная версия (для тестирования)

### Локальная сборка

Для локальной сборки выполните:

```bash
cd frontend
npm install
npm run build
npm run dist:win
```

Результат будет в `frontend/dist-electron/`

## Структура сборки

### Electron приложение включает:

1. **React Frontend** - собранное React приложение из `frontend/build/`
2. **Python Backend** - упакованный через PyInstaller в `python-backend/api-server/`
3. **FFmpeg** - бинарники FFmpeg для обработки видео
4. **Исходный код Python** - для fallback режима (если упакованный backend не работает)

### Требования для сборки

- Node.js 20+
- Python 3.10+
- Windows (для сборки Windows версии)
- Все зависимости из `requirements.txt`

## Настройка секретов GitHub

Для работы диаризации необходимо установить секрет `HF_TOKEN` в настройках GitHub репозитория:
1. Settings → Secrets and variables → Actions
2. Добавить новый секрет `HF_TOKEN` с вашим Hugging Face токеном

## Устранение проблем

### Ошибка при сборке Python backend

Если PyInstaller не может собрать backend:
1. Проверьте, что все зависимости установлены: `pip install -r requirements.txt`
2. Убедитесь, что FFmpeg скачан и находится в `ffmpeg/`
3. Проверьте логи сборки в GitHub Actions

### Большой размер установщика

Размер установщика может быть большим из-за:
- PyTorch и ML модели
- WhisperX и зависимости
- FFmpeg бинарники
- Все Python зависимости

Это нормально для ML приложений.

## Обновление версии

Для обновления версии приложения измените `version` в `frontend/package.json`.
