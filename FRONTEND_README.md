# AI Dubbing Studio - React Frontend

React приложение, созданное точно по дизайну из Pencil, для AI Dubbing Studio.

## Структура

```
frontend/
├── public/
│   └── index.html
├── src/
│   ├── components/
│   │   ├── Header.js          # Шапка с логотипом
│   │   ├── VideoSource.js     # Панель загрузки видео
│   │   ├── AdditionalOptions.js  # Панель настроек
│   │   ├── StartButton.js    # Кнопка запуска
│   │   └── Terminal.js        # Терминал для логов
│   ├── App.js                 # Главный компонент
│   ├── App.css                # Общие стили
│   ├── AppContext.js          # Context для состояния
│   ├── api.js                 # API клиент
│   ├── index.js               # Точка входа
│   └── index.css              # Глобальные стили
├── package.json
└── README.md
```

## Установка

1. Установите зависимости:
```bash
cd frontend
npm install
```

2. Установите Python зависимости для API сервера:
```bash
pip install flask flask-cors
```

## Запуск

### Вариант 1: Автоматический запуск (рекомендуется)

```bash
./start_frontend.sh
```

Этот скрипт автоматически:
- Проверяет установку Node.js
- Устанавливает зависимости React (если нужно)
- Запускает Python API сервер
- Запускает React приложение

### Вариант 2: Ручной запуск

1. Запустите API сервер:
```bash
python3 src/api_server.py
```

2. В другом терминале запустите React:
```bash
cd frontend
npm start
```

Приложение откроется на http://localhost:3000

## API Endpoints

- `GET /api/health` - Проверка здоровья API
- `GET /api/logs` - Получить логи
- `POST /api/logs/clear` - Очистить логи
- `POST /api/process/youtube` - Обработка YouTube видео
- `POST /api/process/file` - Обработка загруженного файла
- `GET /api/status` - Статус обработки

## Дизайн

UI точно соответствует макету из Pencil:
- Темная тема (#0F0F0F, #1A1A1A)
- Желтые акценты (#FFD600)
- Шрифты: Space Grotesk, IBM Plex Mono
- Material Symbols Icons

## Сборка для продакшена

```bash
cd frontend
npm run build
```

Собранные файлы будут в папке `build/`
