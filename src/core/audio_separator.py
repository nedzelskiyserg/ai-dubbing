# -*- coding: utf-8 -*-
"""
Audio Separator Module using Demucs (torchaudio)
Разделение аудио на вокал и инструменты для улучшения качества клонирования голоса.
"""
import os
import sys
import logging
import torch
import torchaudio
from pathlib import Path
from typing import Dict, Optional, Callable

from core.config import APP_PATHS


class AudioSeparator:
    """
    Разделение аудио на вокал и инструменты с помощью HDemucs (torchaudio).
    Вокал используется для клонирования голоса, инструменты — подмешиваются в финал.
    """

    def __init__(
        self,
        progress_callback: Optional[Callable[[str], None]] = None,
    ):
        self.progress_callback = progress_callback
        self.model = None
        self.device = self._detect_device()

        # Директория для кэша разделённых файлов
        self.output_dir = APP_PATHS['temp'] / "demucs"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _log(self, msg: str):
        print(msg)
        if self.progress_callback:
            self.progress_callback(msg)

    def _detect_device(self) -> str:
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    def _load_model(self):
        """Ленивая загрузка модели HDemucs."""
        if self.model is not None:
            return

        self._log(f"📦 Загрузка модели Demucs (HDemucs) на {self.device}...")

        try:
            bundle = torchaudio.pipelines.HDEMUCS_HIGH_MUSDB_PLUS
            self.model = bundle.get_model().to(self.device)
            self.model.eval()
            self._sample_rate = bundle.sample_rate  # 44100
            self._log(f"✅ Demucs загружен (sample rate: {self._sample_rate} Гц)")
        except Exception as e:
            self._log(f"❌ Ошибка загрузки Demucs: {e}")
            raise

    def separate(self, audio_path: str) -> Dict[str, str]:
        """
        Разделяет аудио на вокал и инструменты.

        Args:
            audio_path: Путь к исходному аудио/видео файлу

        Returns:
            {"vocals": path, "instruments": path}
        """
        vocals_path = self.output_dir / "vocals.wav"
        instruments_path = self.output_dir / "instruments.wav"

        # Если уже разделено — используем кэш
        if vocals_path.exists() and instruments_path.exists():
            self._log("✅ Разделённые дорожки найдены в кэше")
            return {
                "vocals": str(vocals_path),
                "instruments": str(instruments_path),
            }

        self._load_model()

        self._log(f"🎵 Разделение аудио на вокал и инструменты...")

        # Загружаем аудио
        try:
            waveform, sample_rate = torchaudio.load(audio_path)
        except Exception:
            # Если torchaudio не может загрузить напрямую (видеофайл), используем pydub
            self._log("   Конвертация видео → WAV...")
            from pydub import AudioSegment
            audio = AudioSegment.from_file(audio_path)
            temp_wav = self.output_dir / "temp_input.wav"
            audio.export(str(temp_wav), format="wav")
            waveform, sample_rate = torchaudio.load(str(temp_wav))
            temp_wav.unlink(missing_ok=True)

        # Ресэмплинг до 44100 Гц (требование HDemucs)
        if sample_rate != self._sample_rate:
            self._log(f"   Ресэмплинг {sample_rate} → {self._sample_rate} Гц...")
            resampler = torchaudio.transforms.Resample(sample_rate, self._sample_rate)
            waveform = resampler(waveform)

        # HDemucs ожидает стерео (2 канала)
        if waveform.shape[0] == 1:
            waveform = waveform.repeat(2, 1)
        elif waveform.shape[0] > 2:
            waveform = waveform[:2]

        # Добавляем batch dimension: (channels, samples) -> (1, channels, samples)
        waveform = waveform.unsqueeze(0).to(self.device)

        duration_sec = waveform.shape[-1] / self._sample_rate
        self._log(f"   Длительность: {duration_sec:.1f} сек, устройство: {self.device}")

        # Разделяем по чанкам (для экономии памяти на длинных аудио)
        # HDemucs работает лучше с чанками ~10 секунд с перекрытием
        segment_length = 10 * self._sample_rate  # 10 секунд
        overlap = 1 * self._sample_rate  # 1 секунда перекрытия

        total_samples = waveform.shape[-1]

        if total_samples <= segment_length:
            # Короткое аудио — обрабатываем целиком
            with torch.no_grad():
                sources = self.model(waveform)
            # sources shape: (batch, num_sources, channels, samples)
            # Sources: 0=drums, 1=bass, 2=other, 3=vocals
        else:
            # Длинное аудио — обрабатываем чанками
            self._log(f"   Обработка чанками по {segment_length // self._sample_rate} сек...")
            sources = self._process_in_chunks(waveform, segment_length, overlap)

        # Извлекаем стемы
        # HDemucs sources order: drums(0), bass(1), other(2), vocals(3)
        vocals = sources[0, 3]        # (channels, samples)
        drums = sources[0, 0]
        bass = sources[0, 1]
        other = sources[0, 2]

        # Инструменты = drums + bass + other
        instruments = drums + bass + other

        # Переносим на CPU для сохранения
        vocals = vocals.cpu()
        instruments = instruments.cpu()

        # Сохраняем
        torchaudio.save(str(vocals_path), vocals, self._sample_rate)
        torchaudio.save(str(instruments_path), instruments, self._sample_rate)

        vocals_dur = vocals.shape[-1] / self._sample_rate
        self._log(f"✅ Разделение завершено:")
        self._log(f"   🎤 Вокал: {vocals_path} ({vocals_dur:.1f}с)")
        self._log(f"   🎸 Инструменты: {instruments_path} ({vocals_dur:.1f}с)")

        # Освобождаем GPU память
        if self.device == "cuda":
            del sources, waveform
            torch.cuda.empty_cache()

        return {
            "vocals": str(vocals_path),
            "instruments": str(instruments_path),
        }

    def _process_in_chunks(self, waveform, segment_length, overlap):
        """Обработка длинного аудио чанками с перекрытием."""
        total_samples = waveform.shape[-1]
        step = segment_length - overlap

        # Первый проход — определяем количество источников
        with torch.no_grad():
            test_chunk = waveform[..., :min(segment_length, total_samples)]
            test_out = self.model(test_chunk)
            num_sources = test_out.shape[1]
            num_channels = test_out.shape[2]

        # Инициализируем накопители
        output = torch.zeros(1, num_sources, num_channels, total_samples, device=self.device)
        weight = torch.zeros(1, 1, 1, total_samples, device=self.device)

        chunk_count = 0
        for start in range(0, total_samples, step):
            end = min(start + segment_length, total_samples)
            chunk = waveform[..., start:end]

            # Паддим если чанк слишком короткий
            if chunk.shape[-1] < segment_length:
                pad_size = segment_length - chunk.shape[-1]
                chunk = torch.nn.functional.pad(chunk, (0, pad_size))

            with torch.no_grad():
                chunk_sources = self.model(chunk)

            # Обрезаем паддинг
            actual_len = end - start
            output[..., start:end] += chunk_sources[..., :actual_len]
            weight[..., start:end] += 1.0

            chunk_count += 1
            progress = min(end / total_samples * 100, 100)
            self._log(f"   Чанк {chunk_count}: {progress:.0f}%")

        # Усредняем перекрытия
        output = output / weight.clamp(min=1.0)
        return output

    def cleanup(self):
        """Очистка временных файлов и GPU памяти."""
        if self.model is not None:
            del self.model
            self.model = None
            if self.device == "cuda":
                torch.cuda.empty_cache()

        # Удаляем временные файлы
        for f in self.output_dir.glob("temp_*"):
            f.unlink(missing_ok=True)
