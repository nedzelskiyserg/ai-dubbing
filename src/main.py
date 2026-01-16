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

# Добавляем путь к src в sys.path для правильной работы импортов
if getattr(sys, 'frozen', False):
    # Если запущено из exe файла
    base_path = sys._MEIPASS
    # nicegui-pack упаковывает файлы в корень, не в src/
    src_path = base_path
    
    # КРИТИЧЕСКИ ВАЖНО: Исправляем sys.argv[0] для NiceGUI
    # NiceGUI использует runpy.run_path(sys.argv[0]), но в exe sys.argv[0] указывает на exe файл
    # Нужно указать путь к исходному Python файлу
    original_argv0 = sys.argv[0]
    
    # Список возможных путей к main.py в exe
    possible_paths = [
        os.path.join(base_path, 'main.py'),
        os.path.join(base_path, 'src', 'main.py'),
        os.path.join(base_path, '__main__.py'),
    ]
    
    # Ищем существующий файл main.py
    main_py_path = None
    for path in possible_paths:
        if os.path.exists(path):
            main_py_path = path
            break
    
    if main_py_path:
        sys.argv[0] = main_py_path
        safe_print(f"Исправлен sys.argv[0]: {original_argv0} -> {sys.argv[0]}")
    else:
        # Если файл не найден, создаем временный файл или используем текущий
        safe_print(f"ВНИМАНИЕ: main.py не найден в {base_path}")
        safe_print(f"Содержимое {base_path}:")
        try:
            for item in os.listdir(base_path):
                safe_print(f"  - {item}")
        except Exception as e:
            safe_print(f"Ошибка при чтении директории: {e}")
        # Используем текущий файл как fallback
        sys.argv[0] = os.path.join(base_path, '__main__.py')
else:
    # Если запущено из исходников
    base_path = os.path.dirname(os.path.abspath(__file__))
    src_path = base_path
    # Убеждаемся, что sys.argv[0] указывает на правильный файл
    if not os.path.exists(sys.argv[0]) or not sys.argv[0].endswith('.py'):
        sys.argv[0] = os.path.abspath(__file__)

if src_path not in sys.path:
    sys.path.insert(0, src_path)

try:
    from nicegui import ui
    from ui import build_interface
except ImportError as e:
    safe_print(f"Ошибка импорта: {e}")
    safe_print(f"sys.path: {sys.path}")
    raise

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