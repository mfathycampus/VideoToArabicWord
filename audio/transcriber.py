"""التفريغ الصوتي المحلي.

التصحيحان الجوهريان مقابل الحزمة الأصلية:

1. **الـ fallback يغطي التنفيذ لا التحميل فقط.** ``try/except`` الأصلي
   يلفّ ``_load_model()`` وحده، بينما أخطاء CUDA OOM و cuDNN تقع غالبًا
   أثناء ``.transcribe()``. المواصفة نفسها تنص على "فشل التحميل
   **/التنفيذ**" — والكود كان ينفّذ نصفها.

2. **إعدادات عربية حاسمة**: ``condition_on_previous_text=False`` يمنع
   حلقات التكرار الهلوسي على فترات الصمت، وهي أشهر أعطال Whisper مع
   العربية. ولأن ``segments`` مولّد كسول (generator)، فإن الاستهلاك
   داخل try هو ما يجعل الالتقاط ممكنًا أصلًا.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, List, Optional

from faster_whisper import WhisperModel

from audio.text_cleaner import clean_segment_text
from config.schemas import AudioSegment, TranscriptionResult, WordTimestamp
from config.settings import TranscriptionConfig
from core.exceptions import ResourceAllocationError
from utils.cancellation import CancellationToken
from utils.gpu_manager import GPUManager
from utils.logger import logger
from utils.timestamps import estimate_remaining, humanize_duration

# أنماط أخطاء تستدعي السقوط إلى CPU
_GPU_ERROR_MARKERS = (
    "cuda", "cublas", "cudnn", "out of memory", "device", "gpu", "nvrtc",
)


def _is_gpu_error(exc: BaseException) -> bool:
    message = f"{type(exc).__name__} {exc}".lower()
    return any(marker in message for marker in _GPU_ERROR_MARKERS)


class TranscriptionEngine:
    def __init__(self, config: Optional[TranscriptionConfig] = None) -> None:
        self.config = config or TranscriptionConfig()
        # الإنتاج يُسقط النموذج بعد كل مهمة لتحرير VRAM. أدوات القياس
        # الدفعية تضبط هذه الراية لتحميله مرة واحدة.
        self.keep_model_loaded = False
        # حاجز ثانٍ ضد التنزيل الصامت (ADR-014): حتى لو أخطأ فحص التوفّر،
        # ``local_files_only`` يمنع faster-whisper من الاتصال بالشبكة.
        # يرفعه الـ pipeline فقط بعد إذن صريح من المستخدم.
        self.allow_download = False
        self._cached: Optional[tuple[str, str, WhisperModel]] = None
        if self.config.download_root is None:
            from audio.model_manager import default_cache_root
            self.config = self.config.model_copy(
                update={"download_root": default_cache_root()})

    def _effective_prompt(self) -> Optional[str]:
        """يدمج القاموس في موجّه البداية.

        ‏Whisper يميل إلى كتابة الأسماء المنقولة كما يسمعها؛ ذكرها في
        الموجّه يجعلها متاحة في سياقه فيختارها بدل تخمين إملائها.
        الحدّ 224 رمزًا في Whisper — نقصّ القاموس بحدّ حروف محافظ حتى لا
        يزيح الموجّه الأساسي.
        """
        base = (self.config.initial_prompt or "").strip()
        glossary = " ".join((self.config.glossary or "").split())
        if not glossary:
            return base or None
        glossary = glossary[:400]
        merged = f"{base} المصطلحات الواردة: {glossary}." if base \
            else f"المصطلحات الواردة: {glossary}."
        return merged

    def _resolve_threads(self) -> int:
        """عدد الخيوط: الإعداد إن حُدّد، وإلا أنوية المعالج الفعلية.

        الرقم الثابت لا يناسب جهازين مختلفين: أربعة خيوط على معالج
        بثمانية أنوية تترك نصف الأداء، وعلى نواتين تُثقِله بتبديل سياق.
        نترك نواةً للنظام والواجهة حتى لا يتجمّد الجهاز أثناء ساعات
        التفريغ.
        """
        configured = int(self.config.cpu_threads or 0)
        if configured > 0:
            return configured
        import os

        cores = os.cpu_count() or 4
        return max(1, cores - 1) if cores > 2 else cores

    def _load_model(self, device: str, compute_type: str) -> WhisperModel:
        if (self.keep_model_loaded and self._cached
                and self._cached[0] == device and self._cached[1] == compute_type):
            return self._cached[2]
        threads = self._resolve_threads()
        logger.info(f"تحميل Whisper ({self.config.model_size}) على "
                    f"{device}/{compute_type} · خيوط={threads} · "
                    f"beam={self.config.beam_size}")

        # تشخيص صريح لأخطر سبب بطء غير مرئي: بيئتا OpenMP في عملية واحدة.
        # بلا هذا السطر يظهر العطل كـ«التفريغ بطيء بلا سبب» ولا شيء
        # يدلّ عليه في السجلّ.
        import sys as _sys

        if "torch" in _sys.modules:
            logger.warning(
                "‏PyTorch محمَّل في هذه العملية مع CTranslate2 — بيئتا "
                "OpenMP تتنازعان الأنوية وقد يتضاعف زمن التفريغ. "
                "لا تُفعّل المحرّكات الاختيارية إلا عند استعمالها فعلًا.")
        model = WhisperModel(
            self.config.model_size,
            device=device,
            compute_type=compute_type,
            cpu_threads=self._resolve_threads(),
            download_root=str(self.config.download_root)
            if self.config.download_root else None,
            local_files_only=not self.allow_download,
        )
        if self.keep_model_loaded:
            self._cached = (device, compute_type, model)
        return model

    def transcribe(
        self,
        audio_path: Path,
        cancel_token: Optional[CancellationToken] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> TranscriptionResult:
        device, compute_type = GPUManager.resolve_device_config(
            self.config.device, self.config.compute_type)
        fallback_used = False

        try:
            return self._run(audio_path, device, compute_type,
                             cancel_token, progress_callback, fallback_used)
        except Exception as exc:
            # الإلغاء ليس خطأ عتاد — يُمرَّر كما هو
            from core.exceptions import PipelineCancelledError
            if isinstance(exc, PipelineCancelledError):
                raise
            if device != "cuda" or not _is_gpu_error(exc):
                raise ResourceAllocationError(f"فشل التفريغ الصوتي: {exc}") from exc

            logger.warning(f"فشل على CUDA أثناء التحميل أو التنفيذ ({exc}); السقوط إلى CPU.")
            GPUManager.release_memory()
            device, compute_type = GPUManager.resolve_device_config("cpu")
            fallback_used = True
            if progress_callback:
                progress_callback(0.0, "تعذّر استخدام كرت الشاشة — التحويل إلى المعالج.")
            return self._run(audio_path, device, compute_type,
                             cancel_token, progress_callback, fallback_used)

    # ------------------------------------------------------------------
    def _run(
        self,
        audio_path: Path,
        device: str,
        compute_type: str,
        cancel_token: Optional[CancellationToken],
        progress_callback: Optional[Callable[[float, str], None]],
        fallback_used: bool,
    ) -> TranscriptionResult:
        model: Optional[WhisperModel] = None
        try:
            model = self._load_model(device, compute_type)
            cfg = self.config
            segments_iter, info = model.transcribe(
                str(audio_path),
                language=cfg.language,
                vad_filter=cfg.vad_filter,
                word_timestamps=cfg.word_timestamps,
                beam_size=cfg.beam_size,
                # حاسم للعربية: يمنع تسرّب السياق وحلقات التكرار
                condition_on_previous_text=cfg.condition_on_previous_text,
                no_speech_threshold=cfg.no_speech_threshold,
                compression_ratio_threshold=cfg.compression_ratio_threshold,
                initial_prompt=self._effective_prompt(),
            )

            total = float(getattr(info, "duration", 0.0) or 0.0)
            started_at = time.monotonic()
            if total > 0:
                logger.info(f"مدة الصوت {humanize_duration(total)} — بدء التفريغ")
            segments: List[AudioSegment] = []
            words: List[WordTimestamp] = []
            raw_parts: List[str] = []
            clean_parts: List[str] = []
            last_emit = -1.0

            # استهلاك المولّد داخل try — هنا تقع أخطاء CUDA فعليًا
            for index, seg in enumerate(segments_iter, start=1):
                if cancel_token:
                    cancel_token.raise_if_cancelled()
                    cancel_token.wait_if_paused()

                raw = seg.text.strip()
                clean = clean_segment_text(raw)
                raw_parts.append(raw)
                if clean:
                    clean_parts.append(clean)

                seg_words = [
                    WordTimestamp(word=w.word.strip(), start=w.start, end=w.end,
                                  probability=getattr(w, "probability", None))
                    for w in (seg.words or [])
                ]
                words.extend(seg_words)
                segments.append(AudioSegment(
                    id=index, start=seg.start, end=seg.end,
                    text_raw=raw, text_clean=clean, words=seg_words,
                ))

                # خنق التحديثات: مرة كل ثانيتين من زمن الفيديو، لا كل مقطع
                if progress_callback and total > 0 and seg.end - last_emit >= 2.0:
                    last_emit = seg.end
                    fraction = min(1.0, seg.end / total)
                    # الوقت المتبقي: بدونه تبدو النسبة الزاحفة وكأن
                    # البرنامج متجمد، والمستخدم يلغي عملًا سليمًا
                    remaining = estimate_remaining(
                        time.monotonic() - started_at, fraction)
                    eta = (f" · متبقٍ ~{humanize_duration(remaining)}"
                           if remaining is not None else "")
                    progress_callback(
                        fraction,
                        f"التفريغ {fraction * 100:.1f}% "
                        f"({seg.end / 60:.0f} من {total / 60:.0f} دقيقة){eta}")

            return TranscriptionResult(
                language=getattr(info, "language", self.config.language),
                full_text_raw=" ".join(raw_parts),
                full_text_clean=" ".join(clean_parts),
                segments=segments,
                words=words,
                engine={
                    "name": "faster-whisper",
                    "model": self.config.model_size,
                    "device": device,
                    "compute_type": compute_type,
                    "fallback_used": fallback_used,
                },
            )
        finally:
            if not self.keep_model_loaded:
                del model
                GPUManager.release_memory()
