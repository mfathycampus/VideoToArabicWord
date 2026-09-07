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
                 config=None, hf_token: str = "", **_kwargs) -> None:
        self.model_id = model_id
        # الإعداد هو المصدر حين يُمرَّر: الجهاز والرمز يأتيان منه
        if config is not None:
            device = getattr(config, "device", device) or device
            hf_token = hf_token or getattr(config, "hf_token", "") or ""
        self.device = device
        self._explicit_token = (hf_token or "").strip()
        self._model = None
        self._processor = None

    @property
    def hf_token(self) -> str:
        """الرمز من الإعداد أولًا ثم من متغيّر البيئة.

        تقديم الإعداد يجنّب أشهر التباس: ``setx`` لا يؤثر على البرامج
        المفتوحة، فيضبط المستخدم الرمز ويظل يرى «مقيّد الوصول».
        """
        import os

        return (self._explicit_token
                or os.environ.get("HF_TOKEN", "")
                or os.environ.get("HUGGING_FACE_HUB_TOKEN", ""))

    # ------------------------------------------------------------------
    # حزم إلزامية: (اسم الاستيراد، اسم الحزمة على PyPI)
    _REQUIRED = (
        ("torch", "torch"),
        ("transformers", "transformers"),
        ("librosa", "librosa"),
        ("soundfile", "soundfile"),
    )

    def diagnose(self) -> tuple[bool, str]:
        """يفحص كل تبعية على حدة ويعيد سببًا صالحًا للعرض.

        ثلاث مصائد حقيقية يعالجها هذا الفحص:

        1. **مفسّر مختلف.** ``pip install`` في PowerShell قد يصيب مفسّرًا
           غير الذي يشغّل التطبيق، فيرى المستخدم «مثبّتة» في الطرفية
           و«غير مثبّت» في البرنامج. لذلك نطبع مسار المفسّر الجاري.
        2. **‏torch يفشل بغير ``ImportError``.** على ويندوز يرفع أحيانًا
           ``OSError`` (‏DLL ناقصة أو حزمة MSVC) — والفحص القديم كان
           يلتقط ``ImportError`` وحدها، فيتسرّب الخطأ ويظهر «غير مثبّت»
           بلا سبب.
        3. **‏transformers قديمة.** الصنف المطلوب أُضيف في 5.4؛ إصدار
           أقدم يستورد بلا خطأ ثم يفشل عند التحميل.
        """
        import importlib.util
        import sys

        # ⚠ الفحص بـ ``find_spec`` لا ``__import__``.
        #
        # الاستيراد الفعلي كان يُحمّل PyTorch و transformers في العملية
        # **عند فتح البرنامج**، لمجرد كتابة «(غير مثبّت)» بجوار محرّك لم
        # يختره أحد. والأثر ليس بطء إقلاع فحسب: PyTorch يجلب معه بيئة
        # OpenMP خاصة به، و CTranslate2 (محرّك faster-whisper) يستعمل
        # OpenMP أيضًا. بيئتا OpenMP في عملية واحدة تتنازعان الأنوية،
        # فيتضاعف زمن التفريغ على المعالج — وهو ما فسّر قفزة معالجة
        # نفس الملف من ~1:45 إلى أكثر من ثلاث ساعات بعد تثبيت المحرّك
        # الاختياري، **دون اختياره**.
        #
        # ``find_spec`` يجيب على السؤال نفسه (أمثبَّتة الحزمة؟) بلا
        # تحميل أي شيء في العملية.
        missing: list[str] = []
        for module, package in self._REQUIRED:
            try:
                if importlib.util.find_spec(module) is None:
                    missing.append(package)
            except Exception as exc:      # حزمة معطوبة أو DLL ناقصة
                return (False,
                        f"تعذّر فحص «{package}»: {type(exc).__name__}: "
                        f"{str(exc)[:160]}\n"
                        f"المفسّر: {sys.executable}")
        if missing:
            return (False,
                    "حزم ناقصة: " + "، ".join(missing) + "\n"
                    f"ثبّتها بـ:  \"{sys.executable}\" -m pip install "
                    "-r requirements-cohere.txt\n"
                    f"المفسّر الذي يبحث فيه البرنامج: {sys.executable}")

        # الإصدار من بيانات التوزيعة لا من استيراد الحزمة
        try:
            from importlib.metadata import version as package_version

            version = package_version("transformers")
        except Exception:
            version = "0"

        def _at_least(found: str, required: tuple[int, ...]) -> bool:
            parts = []
            for chunk in found.split("."):
                digits = "".join(c for c in chunk if c.isdigit())
                if not digits:
                    break
                parts.append(int(digits))
            return tuple(parts[:len(required)]) >= required

        if not _at_least(version, (5, 4)):
            return (False,
                    f"إصدار transformers ({version}) أقدم من المطلوب. "
                    "الصنف المطلوب أُضيف في 5.4:\n"
                    f"  \"{sys.executable}\" -m pip install -U "
                    "\"transformers>=5.4.0\"")

        return (True, f"جاهز · transformers {version}")

    def is_available(self) -> bool:
        """توفّر التبعيات فقط — لا يُنزّل النموذج ولا يشغّله."""
        return self.diagnose()[0]

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

        # يُمرَّر صراحةً: الاعتماد على اكتشاف huggingface_hub وحده يختلف
        # بين إصداراتها، والإعداد يسبق متغيّر البيئة.
        token = self.hf_token or None
        auth = {"token": token} if token else {}

        try:
            self._processor = AutoProcessor.from_pretrained(
                self.model_id, **auth)
            self._model = CohereAsrForConditionalGeneration.from_pretrained(
                self.model_id,
                dtype=torch.float16 if resolved == "cuda" else torch.float32,
                **auth,
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
                    "     ثم أعد فتح البرنامج (setx لا يؤثر على "
                    "البرامج المفتوحة).\n\n"
                    + ("الرمز الحالي: غير مضبوط.\n\n" if not token else
                       "رمز موجود في HF_TOKEN لكن الوصول ما زال مرفوضًا — "
                       "تأكّد أنك وافقت على شروط النموذج بنفس الحساب.\n\n")
                    + "ستكمل المهمة الآن بمحرّك Whisper الافتراضي."
                ) from exc
            raise ModelUnavailableError(
                f"تعذّر تحميل نموذج Cohere: {message[:300]}") from exc
        self._model.eval()
        self._device = resolved

    # ------------------------------------------------------------------
    def transcribe(self, audio_path: Path,
                   cancel_token: Optional[CancellationToken] = None,
                   progress_callback: Optional[ProgressFn] = None,
                   checkpoint=None, resume=None
                   ) -> TranscriptionResult:
        # ``checkpoint`` و``resume`` مقبولان ومُتجاهَلان: ``supports_resume``
        # يبقى ``False`` هنا، فلا يمرّرهما الـ pipeline أصلًا. وجودهما في
        # التوقيع يُبقي نداءً واحدًا لكل المحرّكات.
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

        for offset in range(0, max(1, len(waveform)), step):
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
