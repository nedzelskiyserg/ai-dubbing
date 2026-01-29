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
    Автоматически определяет 'Документы' пользователя.
    """
    # Получаем домашнюю директорию пользователя (~/)
    home_dir = Path.home()
    
    # Основная папка в Документах
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
            path.mkdir(parents=True, exist_ok=True)
    
    # Папку models рядом с exe не создаём в Program Files (нет прав на запись). Используем только Документы/.../Models.
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).resolve().parent
        exe_str = str(exe_dir).lower()
        # Не вызываем mkdir в защищённых путях (Program Files и т.п.)
        if "program files" not in exe_str and "program files (x86)" not in exe_str:
            try:
                exe_models_dir = exe_dir / "models"
                if not exe_models_dir.exists():
                    exe_models_dir.mkdir(parents=True, exist_ok=True)
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

# Инициализируем пути при импорте
APP_PATHS = get_app_paths()