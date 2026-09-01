"""محرّك Cohere Transcribe Arabic — مُخصَّص للعربية.

نموذج Conformer بـ 2 مليار معامل، رخصة Apache 2.0، وزنه مفتوح فيعمل
محليًا بلا API.

**المقايضة الحقيقية:** يحتاج PyTorch — أي ما يقارب 2.5 جيجابايت من
التبعيات التي حُذفت عمدًا في ADR-012. لذلك هو محرّك **اختياري** يُثبَّت
عند الطلب، ولا يُشحن مع التثبيت الأساسي.

الأرقام المنشورة من Cohere على مجموعتها (لهجات صعبة):
    Cohere Transcribe Arabic   WER 25.87
    Whisper large-v3           WER 36.86
تحسّن نسبي ~30٪. مجموعتنا (FLEURS فصيحة) أسهل بكثير، فالأرقام لا تُقارن
مباشرة — استخدم ``tools/benchmark_asr.py`` على مادتك أنت.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from audio.engines.base import ASREngine, EngineInfo, ProgressFn
from config.schemas import AudioSegment, TranscriptionResult
from core.exceptions import ModelUnavailableError
from utils.cancellation import CancellationToken
from utils.logger import logger

MODEL_ID = "CohereLabs/cohere-transcribe-arabic-07-2026"

# النموذج يعالج مقاطع محدودة الطول، فنقسّم الصوت الطويل
CHUNK_SECONDS = 28.0
CHUNK_OVERLAP = 0.5


class CohereArabicEngine(ASREngine):
    info = EngineInfo(
        name="cohere-arabic",
        label_ar="Cohere Transcribe Arabic — مُخصَّص للعربية",
        backend="transformers",
        approx_size_gb=4.5,
        needs_torch=True,
        languages="العربية والإنجليزية",
        strengths="أدق على اللهجات؛ يحتاج PyTorch وبطاقة رسومية للسرعة",
        install_hint="pip install -r requirements-cohere.txt",
        extra_requirements=["transformers>=5.4.0", "torch", "librosa",
                            "soundfile", "sentencepiece", "accelerate"],
    )

    def __init__(self, model_id: str = MODEL_ID, device: str = "auto",
                 **_kwargs) -> None:
        self.model_id = model_id
        self.device = device
        self._model = None
        self._processor = None

    # ------------------------------------------------------------------
    def is_available(self) -> bool:
        """توفّر التبعيات فقط — لا يُنزّل النموذج ولا يشغّله."""
        try:
            import transformers  # noqa: F401
            import torch  # noqa: F401
            import librosa  # noqa: F401
        except ImportError:
            return False
        return hasattr(__import__("transformers"),
                       "CohereAsrForConditionalGeneration")

    def _load(self):
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoProcessor, CohereAsrForConditionalGeneration
        except ImportError as exc:
            raise ModelUnavailableError(
                "محرّك Cohere يحتاج تبعيات إضافية:\n"
                f"    {self.info.install_hint}\n"
                f"السبب: {exc}") from exc

        resolved = self.device
        if resolved == "auto":
            resolved = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info(f"تحميل {self.model_id} على {resolved}…")

        try:
            self._processor = AutoProcessor.from_pretrained(self.model_id)
            self._model = CohereAsrForConditionalGeneration.from_pretrained(
                self.model_id,
                dtype=torch.float16 if resolved == "cuda" else torch.float32,
            ).to(resolved)
        except Exception as exc:
            message = str(exc)
            if "gated repo" in message or "401" in message or "403" in message:
                # حاجز عملي مهم: الوزن مفتوح الرخصة لكن التنزيل مقيّد
                raise ModelUnavailableError(
                    f"نموذج «{self.model_id}» مقيّد الوصول على HuggingFace.\n\n"
                    "الخطوات:\n"
                    f"  1) افتح https://huggingface.co/{self.model_id}\n"
                    "     واضغط على زر طلب الوصول ووافق على الشروط.\n"
                    "  2) أنشئ رمزًا من https://huggingface.co/settings/tokens\n"
                    '  3) في PowerShell:  setx HF_TOKEN "hf_..."\n'
                    "     ثم أعد فتح البرنامج.\n\n"
                    "حتى ذلك الحين يعمل محرّك Whisper الافتراضي بلا قيود."
                ) from exc
            raise ModelUnavailableError(
                f"تعذّر تحميل نموذج Cohere: {message[:300]}") from exc
        self._model.eval()
        self._device = resolved

    # ------------------------------------------------------------------
    def transcribe(self, audio_path: Path,
                   cancel_token: Optional[CancellationToken] = None,
                   progress_callback: Optional[ProgressFn] = None
                   ) -> TranscriptionResult:
        import librosa
        import torch

        self._load()
        waveform, sample_rate = librosa.load(str(audio_path), sr=16000, mono=True)
        total_seconds = len(waveform) / sample_rate

        step = int((CHUNK_SECONDS - CHUNK_OVERLAP) * sample_rate)
        window = int(CHUNK_SECONDS * sample_rate)
        segments: list[AudioSegment] = []
        pieces: list[str] = []
        started = time.monotonic()

        for index, offset in enumerate(range(0, max(1, len(waveform)), step), start=1):
            if cancel_token:
                cancel_token.raise_if_cancelled()
                cancel_token.wait_if_paused()

            chunk = waveform[offset:offset + window]
            if len(chunk) < sample_rate * 0.2:      # أقل من 0.2s: تجاهل
                break

            inputs = self._processor(chunk, sampling_rate=sample_rate,
                                     return_tensors="pt", language="ar")
            inputs = inputs.to(self._device, dtype=self._model.dtype)
            with torch.no_grad():
                generated = self._model.generate(**inputs, max_new_tokens=440)
            text = self._processor.decode(generated[0] if generated.ndim > 1
                                          else generated,
                                          skip_special_tokens=True).strip()

            start = offset / sample_rate
            end = min(total_seconds, start + len(chunk) / sample_rate)
            if text:
                # الطوابع على مستوى المقطع: النموذج لا يعطي توقيت كلمات،
                # وهو فارق مهم عن Whisper يُذكر صراحةً في نتائج القياس.
                segments.append(AudioSegment(
                    id=len(segments) + 1, start=start, end=end,
                    text_raw=text, text_clean=text))
                pieces.append(text)

            if progress_callback and total_seconds > 0:
                fraction = min(1.0, end / total_seconds)
                elapsed = time.monotonic() - started
                progress_callback(
                    fraction,
                    f"التفريغ (Cohere) {fraction * 100:.0f}% "
                    f"— {elapsed:.0f}s")

        joined = " ".join(pieces)
        return TranscriptionResult(
            language="ar", full_text_raw=joined, full_text_clean=joined,
            segments=segments, words=[],
            engine={"name": "cohere-transcribe-arabic",
                    "model": self.model_id, "device": self._device,
                    "word_timestamps": False})

    def release(self) -> None:
        self._model = None
        self._processor = None
        try:
            import gc

            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
