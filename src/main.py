import sys
import os
import io
from pathlib import Path

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

# Загружаем переменные окружения из .env файла (ДО определения safe_print)
try:
    from dotenv import load_dotenv
    # Ищем .env файл рядом с проектом
    if getattr(sys, 'frozen', False):
        # В exe ищем .env рядом с exe файлом
        env_path = Path(sys.executable).parent / '.env'
    else:
        # В исходниках ищем в корне проекта
        env_path = Path(__file__).parent.parent / '.env'
        if not env_path.exists():
            env_path = Path(__file__).parent.parent.parent / '.env'
    
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    # python-dotenv не установлен, пропускаем
    pass
except Exception:
    # Игнорируем ошибки загрузки .env
    pass

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
        # Проверяем, что файл не содержит null байтов
        try:
            with open(main_py_path, 'rb') as f:
                content = f.read()
                if b'\x00' in content:
                    safe_print(f"ВНИМАНИЕ: файл {main_py_path} содержит null байты, очищаем...")
                    content = content.replace(b'\x00', b'')
                    with open(main_py_path, 'wb') as fw:
                        fw.write(content)
        except Exception as e:
            safe_print(f"Ошибка при проверке файла {main_py_path}: {e}")
        
        sys.argv[0] = main_py_path
        safe_print(f"Исправлен sys.argv[0]: {original_argv0} -> {sys.argv[0]}")
    else:
        # Если файл не найден, создаем временный файл с минимальным кодом
        safe_print(f"ВНИМАНИЕ: main.py не найден в {base_path}")
        safe_print(f"Содержимое {base_path}:")
        try:
            for item in os.listdir(base_path):
                safe_print(f"  - {item}")
        except Exception as e:
            safe_print(f"Ошибка при чтении директории: {e}")
        
        # Создаем временный файл main.py для NiceGUI
        temp_main_py = os.path.join(base_path, 'main.py')
        try:
            # Создаем минимальный файл, который просто импортирует и запускает UI
            temp_content = '''# Temporary main.py for NiceGUI
import sys
import os

# Add current directory to path
if hasattr(sys, "_MEIPASS"):
    sys.path.insert(0, sys._MEIPASS)

from nicegui import ui
from ui import build_interface

if __name__ == "__main__":
    build_interface()
    ui.run(title="AI Dubbing Studio", native=True, reload=False, window_size=(900, 700))
'''
            with open(temp_main_py, 'w', encoding='utf-8') as f:
                f.write(temp_content)
            sys.argv[0] = temp_main_py
            safe_print(f"Создан временный файл: {temp_main_py}")
        except Exception as e:
            safe_print(f"Ошибка при создании временного файла: {e}")
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
    import runpy
except ImportError as e:
    safe_print(f"Ошибка импорта: {e}")
    safe_print(f"sys.path: {sys.path}")
    raise

# КРИТИЧЕСКИ ВАЖНО: Перехватываем runpy.run_path для исправления пути
# NiceGUI вызывает runpy.run_path() внутри себя, и нам нужно убедиться,
# что он использует правильный файл, а не exe
if getattr(sys, 'frozen', False):
    original_run_path = runpy.run_path
    
    def patched_run_path(file_path, *args, **kwargs):
        """Патч для runpy.run_path, который исправляет путь к файлу"""
        # Если передан exe файл или путь с null байтами, используем правильный путь
        if file_path and (not file_path.endswith('.py') or not os.path.exists(file_path)):
            # Ищем правильный файл main.py
            base_path = sys._MEIPASS
            possible_paths = [
                os.path.join(base_path, 'main.py'),
                os.path.join(base_path, 'src', 'main.py'),
                os.path.join(base_path, '__main__.py'),
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    # Проверяем на null байты
                    try:
                        with open(path, 'rb') as f:
                            content = f.read()
                            if b'\x00' in content:
                                safe_print(f"ВНИМАНИЕ: файл {path} содержит null байты, очищаем...")
                                content = content.replace(b'\x00', b'')
                                with open(path, 'wb') as fw:
                                    fw.write(content)
                    except Exception:
                        pass
                    file_path = path
                    break
        
        # Вызываем оригинальную функцию с исправленным путем
        return original_run_path(file_path, *args, **kwargs)
    
    # Применяем патч
    runpy.run_path = patched_run_path
    safe_print("Применен патч для runpy.run_path")

# Главная точка входа
if __name__ in {"__main__", "__mp_main__"}:
    safe_print("--- ЗАПУСК AI DUBBING STUDIO ---")
    
    # Проверяем и запускаем Ollama автоматически (если нужно)
    try:
        from core.ollama_manager import OllamaManager
        ollama_manager = OllamaManager(progress_callback=safe_print)
        # Автоматически запускаем Ollama, если он не запущен
        ollama_manager.ensure_ollama_running(auto_start=True)
    except Exception as e:
        safe_print(f"⚠️ Не удалось проверить Ollama: {e}")
        safe_print("💡 Убедитесь, что Ollama установлен и доступен")
    
    # Строим интерфейс из файла ui.py
    build_interface()
    
    # Запускаем приложение
    # native=True означает, что откроется как отдельное окно (не в браузере)
    # reload=False важно для стабильности при сборке exe
    # fullscreen=True запускает в полноэкранном режиме
    ui.run(
        title="AI Dubbing Studio",
        native=True,
        reload=False,
        fullscreen=True
    )