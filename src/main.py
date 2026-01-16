import sys
import os
import io

# --- FIX WINDOWS CONSOLE ENCODING ---
# Устанавливаем UTF-8 кодировку ДО любых операций с stdout/stderr
# Это лечит ошибку "UnicodeEncodeError: 'charmap' codec..."
os.environ['PYTHONIOENCODING'] = 'utf-8'

def fix_encoding():
    """Устанавливает UTF-8 кодировку для stdout и stderr"""
    if sys.stdout:
        try:
            encoding = getattr(sys.stdout, 'encoding', None)
            if encoding != 'utf-8':
                # Проверяем наличие buffer перед оберткой
                if hasattr(sys.stdout, 'buffer'):
                    sys.stdout = io.TextIOWrapper(
                        sys.stdout.buffer, 
                        encoding='utf-8',
                        errors='replace',  # Заменяем проблемные символы вместо ошибки
                        line_buffering=True
                    )
        except (AttributeError, ValueError, TypeError):
            pass
    
    if sys.stderr:
        try:
            encoding = getattr(sys.stderr, 'encoding', None)
            if encoding != 'utf-8':
                if hasattr(sys.stderr, 'buffer'):
                    sys.stderr = io.TextIOWrapper(
                        sys.stderr.buffer,
                        encoding='utf-8',
                        errors='replace',
                        line_buffering=True
                    )
        except (AttributeError, ValueError, TypeError):
            pass

# Применяем фикс сразу
fix_encoding()

# Безопасная функция print для вывода с UTF-8
def safe_print(*args, **kwargs):
    """Безопасный print, который обрабатывает проблемы с кодировкой"""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        # Если не удалось вывести из-за кодировки, выводим ASCII версию
        safe_args = []
        for arg in args:
            if isinstance(arg, str):
                safe_args.append(arg.encode('ascii', errors='replace').decode('ascii'))
            else:
                safe_args.append(str(arg))
        print(*safe_args, **kwargs)
# ------------------------------------

from nicegui import ui
from ui import build_interface

# Главная точка входа
if __name__ in {"__main__", "__mp_main__"}:
    safe_print("--- ЗАПУСК AI DUBBING STUDIO ---")
    
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