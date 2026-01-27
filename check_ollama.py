#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для проверки готовности Ollama и модели qwen2.5:7b
"""
import requests
import sys
import time

def check_ollama():
    """Проверяет статус Ollama и наличие модели"""
    print("🔍 Проверка Ollama...")
    
    try:
        # Проверяем доступность Ollama
        response = requests.get("http://localhost:11434/api/tags", timeout=5)
        
        if response.status_code != 200:
            print(f"❌ Ollama недоступен (код: {response.status_code})")
            return False
        
        models = response.json().get("models", [])
        model_names = [m.get("name", "") for m in models]
        
        print(f"✅ Ollama запущен")
        print(f"📦 Установлено моделей: {len(model_names)}")
        
        # Проверяем наличие qwen2.5:7b
        qwen_found = any("qwen2.5:7b" in name for name in model_names)
        
        if qwen_found:
            print("✅ Модель qwen2.5:7b найдена")
            return True
        else:
            print("⚠️ Модель qwen2.5:7b не найдена")
            print("💡 Установите модель: ollama pull qwen2.5:7b")
            if model_names:
                print("📦 Доступные модели:")
                for name in model_names[:10]:
                    print(f"   - {name}")
            return False
            
    except requests.exceptions.ConnectionError:
        print("❌ Ollama не запущен")
        print("💡 Запустите Ollama: brew services start ollama")
        return False
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False

def wait_for_model(max_wait=600):
    """Ожидает установки модели (максимум max_wait секунд)"""
    print(f"\n⏳ Ожидание установки модели (максимум {max_wait//60} минут)...")
    
    start_time = time.time()
    check_interval = 10  # Проверяем каждые 10 секунд
    
    while time.time() - start_time < max_wait:
        if check_ollama():
            elapsed = int(time.time() - start_time)
            print(f"\n✅ Модель готова! (ожидание: {elapsed} секунд)")
            return True
        
        time.sleep(check_interval)
        elapsed = int(time.time() - start_time)
        print(f"⏳ Ожидание... ({elapsed} секунд)")
    
    print(f"\n⏰ Превышено время ожидания ({max_wait} секунд)")
    return False

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--wait":
        success = wait_for_model()
        sys.exit(0 if success else 1)
    else:
        success = check_ollama()
        sys.exit(0 if success else 1)
