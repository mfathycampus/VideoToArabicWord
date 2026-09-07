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
from typing import Any, Callable, List, Optional

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
    # يرفعها الـ pipeline بعد إذن صريح من المستخدم فقط (ADR-014).
    # موجودة هنا حتى يقبل ضبطَها كلُّ محرّك، ولو تجاهلها.
    allow_download: bool = False

    #: هل يحفظ هذا المحرّك تقدّمه ويستأنف من حيث انقطع؟
    #:
    #: رايةٌ صريحة لا استنتاج من التوقيع: محرّكٌ يقبل الوسيطين ثم
    #: يتجاهلهما بصمت أسوأ من محرّك يعلن أنه لا يدعمهما — لأن الـ
    #: pipeline سيكتب نقاط حفظ لا يقرؤها أحد، ثم يستأنف من الصفر
    #: بينما يقول للمستخدم إنه استأنف.
    supports_resume: bool = False

    @abstractmethod
    def is_available(self) -> bool:
        """جاهزية المحرّك دون تنزيل أو تشغيل فعلي."""

    def diagnose(self) -> tuple[bool, str]:
        """‏(جاهز؟، السبب). السبب نص عربي يُعرض للمستخدم مباشرةً.

        «غير مثبّت» وحدها ليست تشخيصًا: المستخدم الذي نفّذ أمر التثبيت
        فعلًا يحتاج أن يعرف **أي** حزمة ناقصة، وفي **أي** مفسّر يبحث
        البرنامج عنها — وهو أشهر سبب على الإطلاق: التثبيت أصاب مفسّرًا
        آخر غير الذي يشغّل التطبيق.
        """
        return (True, "جاهز") if self.is_available() else (False, "غير متاح")

    @abstractmethod
    def transcribe(
        self,
        audio_path: Path,
        cancel_token: Optional[CancellationToken] = None,
        progress_callback: Optional[ProgressFn] = None,
        checkpoint: Optional[Callable] = None,
        resume: Optional[Any] = None,
    ) -> TranscriptionResult:
        """يفرّغ ملفًا صوتيًا (16kHz أحادي) ويعيد العقد الموحّد.

        ``checkpoint`` و``resume`` اختياريان، ويجوز تجاهلهما تمامًا —
        وهما موجودان في التوقيع الأساسي حتى يبقى نداء الـ pipeline
        واحدًا لكل المحرّكات. من يتجاهلهما يترك ``supports_resume``
        على ``False``، فلا يُمرَّر إليه شيء أصلًا.
        """

    def release(self) -> None:  # noqa: B027
        """تحرير الذاكرة — يُستدعى بعد كل مهمة.

        غير مجرّدة عمدًا: أغلب المحرّكات لا تملك ذاكرة تحتاج تحريرًا
        صريحًا، وإجبارها على تنفيذ دالة فارغة ضجيج لا فائدة فيه.
        """
