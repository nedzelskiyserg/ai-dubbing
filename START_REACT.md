# Запуск React приложения

## Быстрый старт

```bash
# Вариант 1: Полноэкранный режим как нативное приложение (РЕКОМЕНДУЕТСЯ)
./start_fullscreen.sh

# Вариант 2: Обычный запуск в браузере
./start_frontend.sh

# Вариант 3: Ручной запуск
# Терминал 1: API сервер
source .venv/bin/activate  # если используете venv
python3 src/api_server.py

# Терминал 2: React приложение
cd frontend
npm start
```

## Что было сделано:

✅ Создано React приложение с нуля
✅ Все компоненты точно по макету Pencil
✅ Темная тема с желтыми акцентами
✅ API бэкенд на Flask
✅ Интеграция с существующим Python кодом
✅ Автоматическое обновление логов в терминале
✅ Drag & Drop для файлов
✅ Все настройки работают через Context API

## Структура:

```
frontend/
├── src/
│   ├── components/     # React компоненты
│   ├── App.js         # Главный компонент
│   ├── AppContext.js  # Управление состоянием
│   └── api.js         # API клиент
└── package.json

src/
└── api_server.py      # Flask API сервер
```

## Порты:

- React: http://localhost:3000
- API: http://localhost:5001 (изменен с 5000, так как 5000 часто занят AirPlay на macOS)

## Проверка работы:

```bash
# Проверить API сервер
curl http://localhost:5001/api/health

# Должен вернуть: {"status": "ok"}
```

## Логи:

- API сервер: `/tmp/api_server.log`
- React: вывод в консоль
