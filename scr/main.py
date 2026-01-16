from nicegui import ui

def main():
    # Это твой интерфейс
    ui.label('Привет! Это моя AI озвучка').classes('text-2xl font-bold text-center')
    ui.label('Если ты это видишь на Windows - мы победили!').classes('text-lg')
    ui.button('Тестовая кнопка', on_click=lambda: ui.notify('Работает!'))

# Запуск как приложения
if __name__ in {"__main__", "__mp_main__"}:
    main()
    ui.run(native=True, window_size=(800, 600), title="AI Dubbing App")