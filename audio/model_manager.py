"""إدارة نماذج Whisper: الاكتشاف المحلي والتنزيل بإذن صريح.

ADR-014: النموذج لا يُنزَّل تلقائيًا. ADR-002 يمنع أي اتصال شبكي ضمن
الـ pipeline؛ تنزيل النموذج هو الاستثناء الوحيد، ويشترط إذنًا صريحًا
من المستخدم مرة واحدة.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from core.exceptions import ModelUnavailableError
from utils.logger import logger


@dataclass(frozen=True)
class ModelSpec:
    name: str
    label_ar: str
    approx_size_gb: float
    quality_note: str


# مقاسة فعليًا على FLEURS العربية (مصرية)، 86 ثانية، CPU بنواتين، int8.
# النتيجة كانت مفاجئة: turbo تفوّق على large-v3 في **الدقة والسرعة معًا**
# على العربية، فهو الافتراضي. لا تغيّر الترتيب دون إعادة القياس.
#
#   النموذج           WER      دقة الكلمات   دقة الحروف   معامل الزمن
#   large-v3-turbo    7.46%      92.54%        98.64%       1.28x
#   large-v3          9.70%      90.30%        98.18%       3.33x
#   medium           12.69%      87.31%        97.43%       2.78x
AVAILABLE_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec("large-v3-turbo", "large-v3-turbo — الأدق والأسرع (موصى به)", 1.7,
              "دقة كلمات 92.5% · أسرع 2.6× من large-v3"),
    ModelSpec("large-v3", "large-v3 — كامل، أبطأ", 3.1,
              "دقة كلمات 90.3% · قد يتفوّق على تسجيلات صعبة جدًا"),
    ModelSpec("medium", "medium — أخف حجمًا", 1.5,
              "دقة كلمات 87.3% · للأجهزة محدودة المساحة"),
    ModelSpec("small", "small — مسودات فقط", 0.5,
              "سريع لكن دقته منخفضة على العربية"),
)

DEFAULT_MODEL = "large-v3-turbo"

# معامل الزمن الحقيقي المقاس (ثانية معالجة لكل ثانية صوت) على CPU بنواتين.
# يُستخدم لتقدير المدة قبل بدء العمل — بطاقة رسومية تقسمه على 5–15.
MEASURED_RTF_CPU: dict[str, float] = {
    "large-v3-turbo": 1.28,
    "large-v3": 3.33,
    "medium": 2.78,
    "small": 1.0,
}


def estimate_processing_seconds(model_name: str, audio_seconds: float,
                                on_gpu: bool = False) -> float:
    """تقدير زمن التفريغ قبل بدئه، ليعرف المستخدم ما ينتظره."""
    rtf = MEASURED_RTF_CPU.get(model_name, 3.0)
    if on_gpu:
        rtf /= 8.0
    return audio_seconds * rtf


# مجلدات مخبأ HuggingFace تأخذ الشكل:
#   models--Systran--faster-whisper-large-v3-turbo/snapshots/<sha>/model.bin
# وبعض الإعدادات تضع اسم النموذج مجلدًا مباشرًا. النمط يغطي الحالتين.
_MODEL_DIR_RE = re.compile(r"(?:faster-whisper|whisper)-(?P<name>[a-z0-9.\-]+)$")


def _matches_model_dir(path: Path, name: str) -> bool:
    """هل يعود هذا المسار لنموذج اسمه ``name`` بالضبط؟"""
    target = name.strip().lower()
    for part in path.parts:
        token = part.replace("--", "-").strip("-").lower()
        if token == target:
            return True
        match = _MODEL_DIR_RE.search(token)
        if match and match.group("name") == target:
            return True
    return False


def default_cache_root() -> Path:
    env = os.environ.get("VIDEO_AI_DOC_MODELS")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "video_ai_doc" / "whisper"


class ModelManager:
    def __init__(self, cache_root: Optional[Path] = None) -> None:
        self.cache_root = cache_root or default_cache_root()

    def spec(self, name: str) -> ModelSpec:
        for candidate in AVAILABLE_MODELS:
            if candidate.name == name:
                return candidate
        raise ModelUnavailableError(f"نموذج غير معروف: {name}")

    def is_available(self, name: str) -> bool:
        """يفحص وجود النموذج محليًا دون أي اتصال شبكي.

        المطابقة **دقيقة** لا جزئية. المطابقة الجزئية القديمة
        (``name in str(path)``) كانت تعتبر ``large-v3`` مثبّتًا لمجرد
        وجود ``large-v3-turbo``، لأن الأول مقطع فرعي من الثاني. النتيجة:
        تمرّ ``ensure_available`` بلا سؤال، ثم ينزّل faster-whisper
        3.1 جيجابايت صامتًا — خرقٌ مباشر لـ ADR-014 و ADR-002.
        """
        root = self.cache_root
        if not root.exists():
            return False
        return any(_matches_model_dir(path.parent, name)
                   for path in root.rglob("model.bin"))

    def free_space_gb(self) -> float:
        root = self.cache_root
        while not root.exists() and root.parent != root:
            root = root.parent
        return shutil.disk_usage(root).free / (1024 ** 3)

    def ensure_available(
        self,
        name: str,
        allow_download: bool = False,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Path:
        """يضمن توفّر النموذج. لا ينزّل إلا بـ ``allow_download=True``."""
        spec = self.spec(name)
        if self.is_available(name):
            logger.info(f"النموذج {name} متوفر محليًا.")
            return self.cache_root

        if not allow_download:
            raise ModelUnavailableError(
                f"النموذج «{spec.label_ar}» غير مثبّت.\n"
                f"الحجم المطلوب: ~{spec.approx_size_gb:.1f} جيجابايت.\n"
                f"المسار: {self.cache_root}\n"
                "التنزيل يحتاج اتصالًا بالإنترنت وإذنًا صريحًا منك."
            )

        free = self.free_space_gb()
        if free < spec.approx_size_gb * 1.3:
            raise ModelUnavailableError(
                f"المساحة الحرة غير كافية: {free:.1f}GB متاحة، "
                f"والمطلوب ~{spec.approx_size_gb * 1.3:.1f}GB.")

        if progress_callback:
            progress_callback(
                f"تنزيل النموذج «{spec.label_ar}» (~{spec.approx_size_gb:.1f}GB)… "
                "يُنفَّذ مرة واحدة فقط.")
        logger.info(f"تنزيل النموذج {name} إلى {self.cache_root}")
        self.cache_root.mkdir(parents=True, exist_ok=True)

        try:
            from faster_whisper import WhisperModel
            # التحميل نفسه يجلب النموذج ويخزّنه في download_root
            model = WhisperModel(name, device="cpu", compute_type="int8",
                                 download_root=str(self.cache_root))
            del model
        except Exception as exc:
            raise ModelUnavailableError(
                f"فشل تنزيل النموذج {name}: {exc}") from exc

        if progress_callback:
            progress_callback("اكتمل تنزيل النموذج.")
        return self.cache_root
