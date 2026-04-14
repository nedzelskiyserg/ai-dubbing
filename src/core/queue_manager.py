"""
QueueManager — последовательная очередь видео для обработки.

Архитектура:
- Один фоновый воркер-тред забирает следующий WAITING элемент и вызывает
  существующие process_youtube_sync / process_file_sync СИНХРОННО (а не
  в новом треде), используя глобальный processing_state как канал live-
  прогресса. Это сознательный выбор «wrap, don't refactor»: существующие
  синхронные функции остаются нетронутыми, QueueManager оборачивает их.
- Очередь персистится в JSON. На старте элементы со статусом "processing"
  сбрасываются в "waiting" (приложение упало/закрылось посреди ролика).
- Per-item snapshot настроек: элемент хранит options на момент добавления,
  последующие изменения глобальных настроек не влияют на уже добавленные.
- Отмена одиночного элемента использует существующий processing_state
  ['should_stop'] флаг — тот же механизм, что и кнопка «Стоп».
"""

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional

from core.config import APP_PATHS


# Статусы элементов
STATUS_WAITING = "waiting"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"

# Маппинг current_step из processing_state в публичное имя стадии
STAGE_MAP = {
    "downloading": "download",
    "transcribing": "transcribe",
    "translating": "translate",
    "voice_cloning": "voice",
    "making_video": "render",
}


class QueueItem:
    def __init__(
        self,
        source_type: str,
        source: str,
        options: Dict,
        title: Optional[str] = None,
        quality: str = "1080p",
    ):
        self.id: str = uuid.uuid4().hex[:12]
        self.source_type: str = source_type  # "youtube" | "file"
        self.source: str = source            # URL or file path
        self.quality: str = quality
        self.options: Dict = dict(options or {})
        self.title: str = title or self._derive_title()
        self.status: str = STATUS_WAITING
        self.progress: int = 0
        self.stage: Optional[str] = None
        self.error: Optional[str] = None
        self.created_at: float = time.time()
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None

    def _derive_title(self) -> str:
        if self.source_type == "file":
            return os.path.basename(self.source)
        # YouTube URL — грубая эвристика, UI может переписать потом.
        return self.source.split("?")[0].rstrip("/").split("/")[-1] or self.source

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "source_type": self.source_type,
            "source": self.source,
            "quality": self.quality,
            "options": self._sanitize_options(self.options),
            "title": self.title,
            "status": self.status,
            "progress": self.progress,
            "stage": self.stage,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @staticmethod
    def _sanitize_options(options: Dict) -> Dict:
        """Убирает секреты из options при сериализации в JSON (и по сети)."""
        redacted_keys = {
            "openaiApiKey", "openrouter_api_key", "voicer_api_key",
            "hf_token", "HF_TOKEN",
        }
        return {
            k: ("***" if k in redacted_keys and v else v)
            for k, v in options.items()
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "QueueItem":
        item = cls(
            source_type=data.get("source_type", "youtube"),
            source=data.get("source", ""),
            options=data.get("options", {}) or {},
            title=data.get("title"),
            quality=data.get("quality", "1080p"),
        )
        item.id = data.get("id") or item.id
        item.status = data.get("status", STATUS_WAITING)
        # Если при предыдущем запуске элемент был в processing — его нужно
        # перезапустить с нуля (мы не знаем, на какой точке он упал).
        if item.status == STATUS_PROCESSING:
            item.status = STATUS_WAITING
            item.progress = 0
            item.stage = None
            item.started_at = None
        else:
            item.progress = int(data.get("progress") or 0)
            item.stage = data.get("stage")
        item.error = data.get("error")
        item.created_at = float(data.get("created_at") or time.time())
        item.started_at = data.get("started_at")
        item.finished_at = data.get("finished_at")
        return item


class QueueManager:
    """
    Singleton-менеджер очереди. Используется через get_queue_manager().
    """

    _instance: Optional["QueueManager"] = None

    def __init__(
        self,
        state_ref: Dict,
        processor_youtube: Callable,
        processor_file: Callable,
        log_callback: Callable[[str], None],
        state_file: Optional[Path] = None,
    ):
        self._state_ref = state_ref   # ссылка на processing_state dict
        self._process_youtube = processor_youtube
        self._process_file = processor_file
        self._log = log_callback

        self._items: List[QueueItem] = []
        self._lock = threading.RLock()
        self._worker: Optional[threading.Thread] = None
        self._worker_wake = threading.Event()
        self._running: bool = False  # "насос" очереди включён
        self._paused: bool = False   # пауза после текущего элемента

        self._state_file = state_file or (APP_PATHS["temp"] / "queue.json")
        self._load()

    # ── Persistence ────────────────────────────────────────────────────

    def _load(self):
        try:
            if not self._state_file.exists():
                return
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            with self._lock:
                self._items = [QueueItem.from_dict(d) for d in data.get("items", [])]
        except Exception as e:
            self._log(f"[queue] Failed to load queue state: {e}")

    def _save(self):
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                payload = {"items": [it.to_dict() for it in self._items]}
            tmp = self._state_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._state_file)
        except Exception as e:
            self._log(f"[queue] Failed to save queue state: {e}")

    # ── Public API: mutation ───────────────────────────────────────────

    def add(
        self,
        source_type: str,
        source: str,
        options: Dict,
        title: Optional[str] = None,
        quality: str = "1080p",
    ) -> QueueItem:
        item = QueueItem(source_type, source, options, title=title, quality=quality)
        with self._lock:
            self._items.append(item)
        self._save()
        self._log(f"[queue] Added #{len(self._items)}: {item.title}")
        # Если очередь запущена — разбудить воркер, чтобы он подхватил.
        self._worker_wake.set()
        return item

    def remove(self, item_id: str) -> bool:
        with self._lock:
            before = len(self._items)
            self._items = [
                it for it in self._items
                if not (it.id == item_id and it.status != STATUS_PROCESSING)
            ]
            removed = len(self._items) < before
        if removed:
            self._save()
        return removed

    def reorder(self, item_id: str, to_index: int) -> bool:
        with self._lock:
            src_idx = next((i for i, it in enumerate(self._items) if it.id == item_id), None)
            if src_idx is None:
                return False
            item = self._items[src_idx]
            # Нельзя двигать элемент, который сейчас обрабатывается.
            if item.status == STATUS_PROCESSING:
                return False
            self._items.pop(src_idx)
            to_index = max(0, min(to_index, len(self._items)))
            self._items.insert(to_index, item)
        self._save()
        return True

    def clear_done(self) -> int:
        with self._lock:
            before = len(self._items)
            self._items = [
                it for it in self._items
                if it.status not in (STATUS_DONE, STATUS_ERROR, STATUS_CANCELLED)
            ]
            removed = before - len(self._items)
        if removed:
            self._save()
        return removed

    def cancel_current(self) -> bool:
        """Отменяет текущий активный элемент (ставит should_stop глобально)."""
        with self._lock:
            active = next((it for it in self._items if it.status == STATUS_PROCESSING), None)
        if not active:
            return False
        self._state_ref["should_stop"] = True
        self._log(f"[queue] Cancel requested for active: {active.title}")
        return True

    def start(self) -> bool:
        """Запускает воркер очереди (если не запущен)."""
        with self._lock:
            self._paused = False
            if self._running and self._worker and self._worker.is_alive():
                # Уже работает — просто разбудим на случай, если ждал.
                self._worker_wake.set()
                return True
            self._running = True
            self._worker = threading.Thread(
                target=self._worker_loop, name="QueueWorker", daemon=True
            )
            self._worker.start()
        self._log("[queue] Worker started")
        return True

    def pause(self) -> bool:
        """Поставит очередь на паузу ПОСЛЕ завершения текущего элемента."""
        with self._lock:
            self._paused = True
        self._log("[queue] Pause requested (after current item)")
        return True

    # ── Public API: read ───────────────────────────────────────────────

    def get_state(self) -> Dict:
        """
        Возвращает полный снимок очереди. Для активного элемента — прогресс
        и стадия из глобального processing_state (live).
        """
        with self._lock:
            items_out = []
            for it in self._items:
                d = it.to_dict()
                if it.status == STATUS_PROCESSING:
                    # Подсасываем live-прогресс из global state.
                    d["progress"] = int(self._state_ref.get("progress") or 0)
                    step = self._state_ref.get("current_step")
                    d["stage"] = STAGE_MAP.get(step, step) if step else None
                items_out.append(d)
            return {
                "items": items_out,
                "running": self._running,
                "paused": self._paused,
                "active_id": next(
                    (it.id for it in self._items if it.status == STATUS_PROCESSING),
                    None,
                ),
                "counts": {
                    "waiting": sum(1 for it in self._items if it.status == STATUS_WAITING),
                    "done": sum(1 for it in self._items if it.status == STATUS_DONE),
                    "error": sum(1 for it in self._items if it.status == STATUS_ERROR),
                },
            }

    # ── Worker loop ─────────────────────────────────────────────────────

    def _next_waiting(self) -> Optional[QueueItem]:
        with self._lock:
            for it in self._items:
                if it.status == STATUS_WAITING:
                    return it
            return None

    def _worker_loop(self):
        try:
            while True:
                with self._lock:
                    if self._paused:
                        self._log("[queue] Paused")
                        self._running = False
                        return

                item = self._next_waiting()
                if item is None:
                    # Ждём новых элементов до 2 секунд, потом перепроверяем.
                    # Если за 30 секунд ничего не пришло — выходим (воркер
                    # стартанёт заново при add/start).
                    self._worker_wake.clear()
                    woken = self._worker_wake.wait(timeout=30)
                    if not woken:
                        with self._lock:
                            # Ещё раз проверим под локом, чтобы не проспать add.
                            if not any(it.status == STATUS_WAITING for it in self._items):
                                self._running = False
                                self._log("[queue] Worker idle — exiting")
                                return
                    continue

                self._run_item(item)
        except Exception as e:
            self._log(f"[queue] Worker crashed: {e}")
            import traceback
            self._log(traceback.format_exc())
        finally:
            with self._lock:
                self._running = False

    def _run_item(self, item: QueueItem):
        """Запускает обработку одного элемента синхронно в воркер-треде."""
        with self._lock:
            item.status = STATUS_PROCESSING
            item.started_at = time.time()
            item.progress = 0
            item.stage = None
            item.error = None
        self._save()
        self._log(f"[queue] ▶ Processing: {item.title}")

        # Готовим processing_state к новому элементу. Логи НЕ очищаем
        # специально — пользователь хочет видеть непрерывную ленту.
        self._state_ref["is_processing"] = True
        self._state_ref["current_step"] = None
        self._state_ref["progress"] = 0
        self._state_ref["should_stop"] = False

        error_msg: Optional[str] = None
        try:
            if item.source_type == "youtube":
                self._process_youtube(item.source, item.quality, item.options)
            elif item.source_type == "file":
                self._process_file(item.source, item.options)
            else:
                raise ValueError(f"Unknown source_type: {item.source_type}")
        except Exception as e:
            error_msg = str(e)
            self._log(f"[queue] Item failed: {error_msg}")

        stopped = bool(self._state_ref.get("should_stop"))

        with self._lock:
            item.finished_at = time.time()
            if error_msg:
                item.status = STATUS_ERROR
                item.error = error_msg
            elif stopped:
                item.status = STATUS_CANCELLED
            else:
                item.status = STATUS_DONE
                item.progress = 100
                item.stage = None
        self._save()

        # Сбрасываем глобальное состояние между элементами, чтобы UI видел
        # is_processing=False на мгновение и понял, что элемент сменился.
        self._state_ref["is_processing"] = False
        self._state_ref["current_step"] = None
        self._state_ref["progress"] = 0
        self._state_ref["should_stop"] = False

        self._log(f"[queue] ◼ Finished: {item.title} → {item.status}")


# ── Global singleton accessor ──────────────────────────────────────────

_mgr: Optional[QueueManager] = None
_mgr_lock = threading.Lock()


def get_queue_manager(
    state_ref: Optional[Dict] = None,
    processor_youtube: Optional[Callable] = None,
    processor_file: Optional[Callable] = None,
    log_callback: Optional[Callable[[str], None]] = None,
) -> QueueManager:
    """
    Ленивый синглтон. Первый вызов ДОЛЖЕН передать все зависимости
    (state_ref, processor_youtube, processor_file, log_callback).
    Последующие вызовы могут быть без параметров.
    """
    global _mgr
    with _mgr_lock:
        if _mgr is None:
            if state_ref is None or processor_youtube is None or processor_file is None or log_callback is None:
                raise RuntimeError("QueueManager not initialized: pass dependencies on first call")
            _mgr = QueueManager(
                state_ref=state_ref,
                processor_youtube=processor_youtube,
                processor_file=processor_file,
                log_callback=log_callback,
            )
        return _mgr
