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

## Настройка секретов GitHub (БЕЗОПАСНО)

Для работы диаризации необходимо установить секрет `HF_TOKEN` в настройках GitHub репозитория. Это **безопасный метод** - токен никогда не будет виден в логах или коде.

### Инструкция по установке:

1. Перейдите в ваш GitHub репозиторий
2. Откройте **Settings** → **Secrets and variables** → **Actions**
3. Нажмите **"New repository secret"**
4. В поле **Name** введите: `HF_TOKEN`
5. В поле **Secret** вставьте ваш Hugging Face токен (получить можно на https://huggingface.co/settings/tokens)
6. Нажмите **"Add secret"**

### Безопасность:

✅ Токен хранится в зашифрованном виде в GitHub  
✅ Токен доступен только в GitHub Actions workflows  
✅ Токен **никогда** не попадает в логи или код  
✅ Токен можно отозвать в любой момент через настройки Hugging Face  

⚠️ **ВАЖНО**: Никогда не коммитьте токены напрямую в код или `.env` файлы!

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
