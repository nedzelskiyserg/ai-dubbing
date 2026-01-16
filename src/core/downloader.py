from pytubefix import YouTube
from pytubefix.cli import on_progress
import os
import shutil
import subprocess
import time
import ssl
import certifi
import sys
import platform
# Импортируем наши пути
from core.config import APP_PATHS

# Исправление SSL для Windows
def fix_ssl():
    """Исправляет проблемы с SSL сертификатами на Windows"""
    import platform
    try:
        # Создаем SSL контекст с использованием certifi
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        # Устанавливаем глобальный SSL контекст
        ssl._create_default_https_context = lambda: ssl_context
    except Exception as e:
        # Если certifi не установлен или произошла ошибка, используем контекст без проверки (только для Windows)
        if platform.system() == 'Windows':
            # Создаем контекст без проверки сертификатов для Windows
            ssl._create_default_https_context = ssl._create_unverified_context
        # На других системах оставляем стандартное поведение

# Применяем фикс SSL при импорте
fix_ssl()

def get_ffmpeg_path():
    """Ищет FFmpeg в системе (и внутри .app/.exe при сборке)"""
    is_windows = platform.system() == 'Windows'
    is_frozen = getattr(sys, 'frozen', False)
    
    possible_paths = []
    
    # Если запущено из exe файла
    if is_frozen:
        if is_windows:
            # На Windows exe находится в sys.executable
            exe_dir = os.path.dirname(sys.executable)
        else:
            # На macOS/Linux используем sys._MEIPASS или директорию exe
            exe_dir = os.path.dirname(sys.executable) if hasattr(sys, 'executable') else os.getcwd()
        
        # Ищем рядом с exe файлом
        possible_paths.extend([
            os.path.join(exe_dir, "ffmpeg.exe" if is_windows else "ffmpeg"),
            os.path.join(exe_dir, "ffmpeg", "ffmpeg.exe" if is_windows else "ffmpeg"),
        ])
    
    # Текущая рабочая директория
    cwd = os.getcwd()
    possible_paths.extend([
        os.path.join(cwd, "ffmpeg.exe" if is_windows else "ffmpeg"),
        os.path.join(cwd, "ffmpeg", "ffmpeg.exe" if is_windows else "ffmpeg"),
    ])
    
    # Windows стандартные пути
    if is_windows:
        program_files = os.environ.get('ProgramFiles', 'C:\\Program Files')
        program_files_x86 = os.environ.get('ProgramFiles(x86)', 'C:\\Program Files (x86)')
        local_appdata = os.environ.get('LOCALAPPDATA', os.path.expanduser('~\\AppData\\Local'))
        
        possible_paths.extend([
            os.path.join(program_files, "ffmpeg", "bin", "ffmpeg.exe"),
            os.path.join(program_files_x86, "ffmpeg", "bin", "ffmpeg.exe"),
            os.path.join(local_appdata, "ffmpeg", "bin", "ffmpeg.exe"),
            "ffmpeg.exe",  # В PATH
            "ffmpeg",  # В PATH (на случай если установлен без .exe)
        ])
    else:
        # Unix/Linux/macOS стандартные пути
        possible_paths.extend([
            "/opt/homebrew/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/usr/bin/ffmpeg",
            "ffmpeg",  # В PATH
        ])
    
    # Проверяем каждый путь
    for path in possible_paths:
        # Сначала проверяем через shutil.which (ищет в PATH)
        if shutil.which(path):
            return path
        
        # Затем проверяем существование файла
        if os.path.exists(path):
            # На Windows проверяем доступность по-другому
            if is_windows:
                try:
                    # Пробуем запустить для проверки доступности
                    result = subprocess.run([path, '-version'], 
                                          stdout=subprocess.PIPE, 
                                          stderr=subprocess.PIPE, 
                                          timeout=2)
                    if result.returncode == 0 or result.returncode == 1:  # FFmpeg возвращает 1 при -version
                        return path
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    continue
            else:
                # На Unix-подобных системах проверяем права на выполнение
                if os.access(path, os.X_OK):
                    return path
    
    return None

def download_video(url, log_func, target_quality='1080p'):
    try:
        log_func(f"🔴 (Pytubefix) Ссылка: {url}")
        log_func(f"🎯 Цель: {target_quality}")
        
        # --- ИСПОЛЬЗУЕМ ПРАВИЛЬНЫЙ ПУТЬ ИЗ CONFIG ---
        output_folder = APP_PATHS["downloads"]
        log_func(f"📂 Папка сохранения: {output_folder}")
        # --------------------------------------------

        # 1. Инициализация
        try:
            yt = YouTube(url, on_progress_callback=on_progress)
        except Exception as e:
            log_func(f"❌ Ошибка доступа: {str(e)}")
            return None

        # Очистка имени файла
        safe_title = "".join([c for c in yt.title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
        video_title = safe_title.replace(" ", "_")
        
        log_func(f"🎬 Название: {video_title}")

        # 2. Выбор качества
        all_resolutions = ['4320p', '2160p', '1440p', '1080p', '720p', '480p', '360p']
        search_resolutions = []
        
        if target_quality == 'max':
            search_resolutions = all_resolutions
        else:
            if target_quality in all_resolutions:
                start_index = all_resolutions.index(target_quality)
                search_resolutions = all_resolutions[start_index:]
            else:
                search_resolutions = all_resolutions

        # 3. Поиск видео
        video_stream = None
        for res in search_resolutions:
            stream = yt.streams.filter(res=res, only_video=True).first()
            if stream:
                video_stream = stream
                size_mb = stream.filesize_mb
                log_func(f"💎 Найдено качество: {res} ({size_mb:.1f} MB)")
                break
        
        if not video_stream:
            log_func("⚠️ Выбранное качество не найдено, беру лучшее...")
            video_stream = yt.streams.get_highest_resolution()

        # 4. Поиск аудио
        audio_stream = yt.streams.filter(only_audio=True).order_by("abr").desc().first()

        # Имена файлов (Temp кладем туда же или в папку Temp, давай пока рядом для простоты)
        timestamp = int(time.time())
        temp_video_name = f"temp_v_{timestamp}.mp4"
        temp_audio_name = f"temp_a_{timestamp}.mp4"
        final_filename = f"{video_title}_{video_stream.resolution}.mp4"
        final_path = os.path.join(output_folder, final_filename)

        # 5. Скачивание
        log_func("🚀 Скачивание видео...")
        video_stream.download(output_path=output_folder, filename=temp_video_name)
        
        log_func("🚀 Скачивание аудио...")
        audio_stream.download(output_path=output_folder, filename=temp_audio_name)

        ffmpeg_exe = get_ffmpeg_path()
        if not ffmpeg_exe:
            log_func("❌ FFmpeg не найден! Склейка невозможна.")
            return None

        # 6. Склейка
        log_func("🔨 Сборка файла...")
        video_path = os.path.join(output_folder, temp_video_name)
        audio_path = os.path.join(output_folder, temp_audio_name)
        
        cmd = [
            ffmpeg_exe, '-i', video_path, '-i', audio_path,
            '-c:v', 'copy', '-c:a', 'aac', '-strict', 'experimental',
            '-y', final_path
        ]

        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()

        if process.returncode != 0:
            log_func(f"❌ Ошибка FFmpeg: {stderr.decode()}")
            return None

        # Чистим temp
        if os.path.exists(video_path): os.remove(video_path)
        if os.path.exists(audio_path): os.remove(audio_path)

        log_func(f"✅ УСПЕХ: {final_path}")
        return final_path

    except Exception as e:
        log_func(f"❌ Критическая ошибка: {str(e)}")
        return None