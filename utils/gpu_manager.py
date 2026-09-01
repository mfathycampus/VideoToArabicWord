"""اكتشاف العتاد وإدارة الذاكرة — **بدون PyTorch**.

لماذا حُذف torch:
    ``faster-whisper`` يعمل على **CTranslate2**، لا على PyTorch. الحزمة
    الأصلية تجرّ ``torch==2.4.0`` (عجلة بحجم ~797MB، وتفكّ إلى ~2.5GB
    لبناء CUDA) من أجل استدعاء واحد: ``torch.cuda.is_available()``.

    ``ctranslate2`` (59MB) يوفّر نفس المعلومة أصلًا:
        ct.get_cuda_device_count()
        ct.get_supported_compute_types(device)

    الفائدة: تقارب هدف "< 3GB" في Definition of Done، وتحذف أكبر مصدر
    لمشاكل PyInstaller في المشروع كله.

    ملاحظة: ``empty_cache()`` لا مقابل له في CTranslate2 — تحرير VRAM
    يتم بإسقاط مرجع النموذج ثم ``gc.collect()``. هذا كافٍ ومقصود.
"""

from __future__ import annotations

import gc
from functools import lru_cache
from typing import Tuple

import ctranslate2

from utils.logger import logger


@lru_cache(maxsize=1)
def cuda_device_count() -> int:
    try:
        return ctranslate2.get_cuda_device_count()
    except Exception as exc:  # بيئة بلا برامج تعريف CUDA
        logger.debug(f"تعذر استعلام CUDA: {exc}")
        return 0


class GPUManager:
    @staticmethod
    def is_cuda_available() -> bool:
        return cuda_device_count() > 0

    @staticmethod
    def resolve_device_config(
        preferred_device: str = "auto",
        preferred_compute: str = "auto",
    ) -> Tuple[str, str]:
        """يعيد ``(device, compute_type)`` مدعومَين فعليًا على هذا الجهاز."""
        if preferred_device == "cpu" or not GPUManager.is_cuda_available():
            if preferred_device == "cuda":
                logger.warning("طُلب CUDA وهو غير متاح — التحويل إلى CPU.")
            return "cpu", GPUManager._pick_compute("cpu", preferred_compute)
        return "cuda", GPUManager._pick_compute("cuda", preferred_compute)

    @staticmethod
    def _pick_compute(device: str, preferred: str) -> str:
        """يختار نوع حساب مدعومًا بدل افتراض float16/int8 عمياء.

        بطاقات ما قبل معمارية Turing لا تدعم float16 بكفاءة، وبعض بيئات
        CPU لا تدعم int8 — الافتراض الأعمى يسبب انهيارًا وقت التنفيذ.
        """
        try:
            supported = ctranslate2.get_supported_compute_types(device)
        except Exception:
            supported = set()

        if preferred != "auto" and preferred in supported:
            return preferred

        order = ["float16", "int8_float16", "float32"] if device == "cuda" \
            else ["int8", "int8_float32", "float32"]
        for candidate in order:
            if candidate in supported:
                return candidate
        return "float32"

    @staticmethod
    def release_memory() -> None:
        """تحرير الذاكرة. في CTranslate2 يتم بإسقاط المرجع + جمع القمامة."""
        gc.collect()
        logger.debug("تم تشغيل جمع القمامة لتحرير ذاكرة النموذج.")
