from nicegui import ui
from ui import build_interface  # <--- УБРАЛИ "src."

@ui.page('/')
def index():
    build_interface()

if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        native=True,
        window_size=(900, 800),
        title="AI Dubbing Studio",
        reload=False,
        port=native.find_open_port() if 'native' in globals() else 8080,
    )