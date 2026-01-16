import sys
import io

# --- FIX WINDOWS CONSOLE ENCODING ---
# Принудительно заставляем консоль понимать UTF-8 (русский язык и смайлики)
# Это лечит ошибку "UnicodeEncodeError: 'charmap' codec..."
if sys.stdout:
    try:
        encoding = getattr(sys.stdout, 'encoding', None)
        if encoding != 'utf-8':
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    except (AttributeError, ValueError):
        pass

if sys.stderr:
    try:
        encoding = getattr(sys.stderr, 'encoding', None)
        if encoding != 'utf-8':
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
    except (AttributeError, ValueError):
        pass
# ------------------------------------

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