from nicegui import ui
from ui import build_interface

# Главная точка входа
if __name__ in {"__main__", "__mp_main__"}:
    print("--- ЗАПУСК AI DUBBING STUDIO ---")
    
    # Строим интерфейс из файла ui.py
    build_interface()
    
    # Запускаем приложение
    # native=True означает, что откроется как отдельное окно (не в браузере)
    # reload=False важно для стабильности при сборке exe
    ui.run(
        title="AI Dubbing Studio",
        native=True,
        reload=False,
        window_size=(900, 700)
    )