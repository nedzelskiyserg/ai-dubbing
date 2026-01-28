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
    
    # Также создаем папку models рядом с exe (если запущено из exe и есть права на запись)
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).parent
        exe_models_dir = exe_dir / "models"
        # В Program Files нет прав на запись — создаём только если можем, иначе пропускаем
        try:
            if not exe_models_dir.exists():
                exe_models_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            # В Program Files или другом защищённом месте — не создаём папку рядом с exe
            # models будут использоваться из Documents/AI Dubbing Studio/Models (уже создано выше)
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