"""واجهة محرّك التفريغ — تسمح بتبديل النموذج دون لمس بقية النظام.

ADR-017: لا يوجد «أفضل محرّك» مطلق. الأدق على لهجة قد يكون الأبطأ على
جهاز، والأسرع قد يحتاج بطاقة رسومية غير متوفرة. لذلك يُبنى التفريغ خلف
واجهة واحدة، وتُشحن أداة قياس تحسم الاختيار على **مادة المستخدم وجهازه**
بدل الاعتماد على أرقام منشورة على بيانات مختلفة.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from config.schemas import TranscriptionResult
from utils.cancellation import CancellationToken

ProgressFn = Callable[[float, str], None]


@dataclass(frozen=True)
class EngineInfo:
    """بطاقة تعريف المحرّك — تُعرض في الواجهة وتوجّه القياس."""
    name: str
    label_ar: str
    backend: str                 # ctranslate2 | transformers | api
    approx_size_gb: float
    needs_torch: bool
    languages: str
    strengths: str
    install_hint: str = ""
    extra_requirements: List[str] = field(default_factory=list)


class ASREngine(ABC):
    """كل محرّك تفريغ يُنفّذ هذه الواجهة."""

    info: EngineInfo

    @abstractmethod
    def is_available(self) -> bool:
        """جاهزية المحرّك دون تنزيل أو تشغيل فعلي."""

    @abstractmethod
    def transcribe(
        self,
        audio_path: Path,
        cancel_token: Optional[CancellationToken] = None,
        progress_callback: Optional[ProgressFn] = None,
    ) -> TranscriptionResult:
        """يفرّغ ملفًا صوتيًا (16kHz أحادي) ويعيد العقد الموحّد."""

    def release(self) -> None:
        """تحرير الذاكرة — يُستدعى بعد كل مهمة."""
