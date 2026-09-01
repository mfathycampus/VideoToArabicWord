"""محرّك faster-whisper — الافتراضي.

يعمل على CTranslate2 بلا PyTorch (ADR-012)، فهو الأخف تثبيتًا والأسرع
على المعالج. يبقى الافتراضي لأنه يوازن الدقة والسرعة وحجم التثبيت.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from audio.engines.base import ASREngine, EngineInfo, ProgressFn
from audio.transcriber import TranscriptionEngine
from config.schemas import TranscriptionResult
from config.settings import TranscriptionConfig
from utils.cancellation import CancellationToken


class FasterWhisperEngine(ASREngine):
    info = EngineInfo(
        name="faster-whisper",
        label_ar="Whisper (faster-whisper) — الافتراضي",
        backend="ctranslate2",
        approx_size_gb=1.7,
        needs_torch=False,
        languages="99 لغة",
        strengths="خفيف وسريع على المعالج، بلا PyTorch، دقة عالية للفصحى",
    )

    def __init__(self, config: Optional[TranscriptionConfig] = None) -> None:
        self.config = config or TranscriptionConfig()
        self._engine = TranscriptionEngine(self.config)

    @property
    def keep_model_loaded(self) -> bool:
        return self._engine.keep_model_loaded

    @keep_model_loaded.setter
    def keep_model_loaded(self, value: bool) -> None:
        self._engine.keep_model_loaded = value

    def is_available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False

    def transcribe(self, audio_path: Path,
                   cancel_token: Optional[CancellationToken] = None,
                   progress_callback: Optional[ProgressFn] = None
                   ) -> TranscriptionResult:
        return self._engine.transcribe(audio_path, cancel_token,
                                       progress_callback)

    def release(self) -> None:
        self._engine.keep_model_loaded = False
        self._engine._cached = None
