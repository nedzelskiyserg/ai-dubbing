# AI Dubbing Studio - Frontend

React приложение для AI Dubbing Studio, созданное точно по дизайну из Pencil.

## Установка

```bash
npm install
```

## Запуск

```bash
npm start
```

Приложение откроется на http://localhost:3000

## Сборка для продакшена

```bash
npm run build
```

## Структура

- `src/components/` - React компоненты
- `src/App.js` - Главный компонент приложения
- `src/index.js` - Точка входа

## Компоненты

- **Header** - Шапка с логотипом и кнопкой папки
- **VideoSource** - Панель для загрузки видео (YouTube URL или файл)
- **AdditionalOptions** - Панель с настройками (Processing, Translation, Voice Cloning)
- **StartButton** - Кнопка запуска обработки
- **Terminal** - Терминал для вывода логов
