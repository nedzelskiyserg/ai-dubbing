from nicegui import ui
from src.ui import build_interface  # Импортируем нашу функцию дизайна

@ui.page('/')
def index():
    # Строим интерфейс из файла ui.py
    build_interface()

if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        native=True,
        window_size=(900, 800), # Чуть увеличили окно
        title="AI Dubbing Studio",
        reload=False,
        port=native.find_open_port() if 'native' in globals() else 8080,
    )