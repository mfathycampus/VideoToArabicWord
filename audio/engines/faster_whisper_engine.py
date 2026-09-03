"""محرّك faster-whisper — الافتراضي.

يعمل على CTranslate2 بلا PyTorch (ADR-012)، فهو الأخف تثبيتًا والأسرع
على المعالج. يبقى الافتراضي لأنه يوازن الدقة والسرعة وحجم التثبيت.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from audio.engines.base import ASREngine, EngineInfo, ProgressFn
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
        install_hint="ثبّته عبر: pip install faster-whisper",
    )

    def __init__(self, config: Optional[TranscriptionConfig] = None) -> None:
        self.config = config or TranscriptionConfig()
        self._engine = None
        try:
            from audio.transcriber import TranscriptionEngine
            self._engine = TranscriptionEngine(self.config)
        except ImportError:
            # يسمح ذلك للواجهة/doctor بإنشاء بطاقة المحرك وتشخيصه دون اعتماد إلزامي.
            self._engine = None

    @property
    def keep_model_loaded(self) -> bool:
        return bool(self._engine and self._engine.keep_model_loaded)

    @keep_model_loaded.setter
    def keep_model_loaded(self, value: bool) -> None:
        
        if self._engine is not None:
            self._engine.keep_model_loaded = value

    @property
    def allow_download(self) -> bool:
        return bool(self._engine and self._engine.allow_download)

    @allow_download.setter
    def allow_download(self, value: bool) -> None:
        
        if self._engine is not None:
            self._engine.allow_download = value

    def is_available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False

    def diagnose(self) -> tuple[bool, str]:
        if self._engine is None:
            return False, "حزمة faster-whisper غير مثبّتة في هذا المفسّر."
        return True, "جاهز — faster-whisper / CTranslate2"

    def transcribe(self, audio_path: Path,
                   cancel_token: Optional[CancellationToken] = None,
                   progress_callback: Optional[ProgressFn] = None
                   ) -> TranscriptionResult:
        if self._engine is None:
            raise RuntimeError("faster-whisper غير مثبّت. شغّل Doctor للتشخيص.")
        return self._engine.transcribe(audio_path, cancel_token,
                                       progress_callback)

    def release(self) -> None:
        if self._engine is not None:
            self._engine.keep_model_loaded = False
            self._engine._cached = None
