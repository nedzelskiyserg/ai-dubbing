import os
import sys
import platform
import subprocess
from pathlib import Path

# Определяем имя приложения для папки в Документах
APP_NAME = "AI Dubbing Studio"

def get_app_paths():
    """
    Возвращает словарь с путями к рабочим папкам.
    В режиме разработки: Документы/AI Dubbing Studio.
    В собранном приложении (frozen): путь установки или %LOCALAPPDATA%, чтобы не зависеть от «локального» пути разработчика.
    """
    home_dir = Path.home()
    base_dir = home_dir / "Documents" / APP_NAME

    # Собранное приложение (exe/app): данные рядом с программой или в стандартной папке приложения
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).resolve().parent
        exe_str = str(exe_dir).lower()
        if platform.system() == 'Windows':
            # В Program Files писать нельзя — используем %LOCALAPPDATA%\AI Dubbing Studio
            if "program files" in exe_str or "program files (x86)" in exe_str:
                local_appdata = os.environ.get('LOCALAPPDATA')
                if not local_appdata:
                    local_appdata = str(home_dir / 'AppData' / 'Local')
                base_dir = Path(local_appdata) / APP_NAME
            else:
                # Установка не в Program Files (например, в папку пользователя) — данные рядом с exe
                base_dir = exe_dir
        else:
            # macOS/Linux: можно оставить Documents или использовать рядом с .app
            base_dir = home_dir / "Documents" / APP_NAME

    paths = {
        "base": base_dir,
        "downloads": base_dir / "Downloads",
        "output": base_dir / "Output",
        "temp": base_dir / "Temp",
        "models": base_dir / "Models"
    }

    # Создаем папки, если их нет
    for key, path in paths.items():
        if not path.exists():
            try:
                path.mkdir(parents=True, exist_ok=True)
            except (PermissionError, OSError):
                pass

    return paths

def open_folder(path):
    """
    Открывает папку в проводнике (Finder/Explorer)
    """
    path = str(path)
    system_platform = platform.system()
    
    try:
        if system_platform == "Windows":
            os.startfile(path)
        elif system_platform == "Darwin":  # macOS
            subprocess.call(["open", path])
        else:  # Linux
            subprocess.call(["xdg-open", path])
    except Exception as e:
        print(f"Не удалось открыть папку: {e}")

def resolve_path_for_win(path: str) -> str:
    """
    На Windows при длине пути > 260 символов или кириллице в пути
    os.path.exists может не находить файл. Пробуем:
    1) префикс \\?\ (длинные пути);
    2) короткий путь 8.3 (GetShortPathNameW), если файл по-прежнему не найден.
    На остальных ОС возвращает путь как есть.
    """
    if platform.system() != 'Windows':
        return path
    path = os.path.abspath(path)
    # Сначала пробуем длинный формат
    if path.startswith('\\\\?\\'):
        long_path = path
    elif path.startswith('\\\\'):
        long_path = '\\\\?\\UNC\\' + path[2:]
    else:
        long_path = '\\\\?\\' + path
    if os.path.exists(long_path):
        return long_path
    if os.path.exists(path):
        return path
    # Запасной вариант: короткий путь 8.3 (работает при кириллице в имени)
    short_path = _get_short_path_win(long_path)
    if not short_path:
        short_path = _get_short_path_win(path)
    if short_path and os.path.exists(short_path):
        return short_path
    return long_path  # вернём длинный, чтобы ошибка "не найден" показывала исходный путь


def _get_short_path_win(long_path: str):
    """Возвращает короткий путь 8.3 для файла/папки на Windows или None."""
    if platform.system() != 'Windows':
        return None
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        buf_size = 0
        while True:
            buf = ctypes.create_unicode_buffer(buf_size or 512)
            needed = kernel32.GetShortPathNameW(long_path, buf, len(buf))
            if needed == 0:
                return None
            if needed <= len(buf):
                return buf.value
            buf_size = needed
    except Exception:
        return None


# Инициализируем пути при импорте
APP_PATHS = get_app_paths()