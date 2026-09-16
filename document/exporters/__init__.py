"""مُصيِّرات إضافية فوق ``plan.json`` — صيغة إخراج لكل جمهور.

**لماذا هنا بالضبط.** ``DocumentPlan`` عقدٌ مستقل عن صيغة الإخراج
(انظر ``config/schemas``): أقسامٌ وعناوين وملخّصات وكتلٌ نصّية وصور
بتوقيتاتها، مع ``segment_ids`` لكل كتلة. مولّد Word ليس إلا **مُصيِّرًا
واحدًا** لهذا العقد. فكل صيغة أخرى — صفحة ويب، شرائح، فصول فيديو —
مُصيِّرٌ آخر فوق البيانات نفسها، لا مسارًا جديدًا في الـ pipeline.

النتيجة العملية: إضافة صيغة لا تلمس التفريغ ولا كشف المشاهد ولا
المُخطِّط، و``--rebuild`` يُنتجها كلّها في ثوانٍ من خطة محفوظة بدل
ساعات إعادة معالجة.

**عقد الفشل — منقول حرفيًا من ``document/subtitles``:** هذه مخرجات
إضافية لا شروط نجاح. مُصيِّر يسقط يُسجَّل ويُتخطّى، والبقية تُكتب،
والمهمة تنجح. مستند Word وحده هو المخرج الذي يُفشل فشلُه المهمة.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from config.schemas import DocumentPlan, TranscriptionResult, VideoMetadata
from utils.logger import logger


@dataclass
class ExportContext:
    """كل ما قد يحتاجه أي مُصيِّر — يُبنى مرة ويُمرَّر للجميع.

    ``base_path`` مسار مستند Word نفسه، ومنه تُشتقّ أسماء الأخوة
    بـ ``with_suffix``. هذا يضمن أن كل مخرجات المهمة تحمل الاسم نفسه
    باختلاف الامتداد وحده — كما تفعل ملفات الترجمة اليوم.
    """
    plan: DocumentPlan
    metadata: VideoMetadata
    images_dir: Path
    base_path: Path
    #: مستند Word المولَّد. مصدرُ مُصيِّر PDF، وقد يغيب في مسار
    #: ``--transcript-only`` فيتخطّى نفسه بدل أن يسقط.
    docx_path: Optional[Path] = None
    #: التفريغ الخام. ``None`` مقبول: المُصيِّر الذي يحتاجه يتخطّى نفسه.
    transcript: Optional[TranscriptionResult] = None
    #: ملف المصدر الأصلي — يستعمله مُصيِّر HTML لربط المشغّل بالتوقيتات.
    source_media: Optional[Path] = None
    #: ``config.document`` كما هو. الخط والشعار وإظهار التوقيتات
    #: قرارات عرضٍ يشترك فيها كل المُصيِّرات.
    document_config: object = None
    options: Dict[str, object] = field(default_factory=dict)

    def sibling(self, suffix: str) -> Path:
        """مسار أخٍ للمستند بامتداد آخر: ``محاضرة.docx`` ⇒ ``محاضرة.html``."""
        return self.base_path.with_suffix(suffix if suffix.startswith(".")
                                          else f".{suffix}")


#: ``اسم الصيغة -> دالة تكتب الملف وتعيد مساره (أو None إن تخطّت نفسها)``
Exporter = Callable[[ExportContext], Optional[Path]]

_REGISTRY: Dict[str, Exporter] = {}


def register(name: str, exporter: Exporter) -> None:
    _REGISTRY[name.lower()] = exporter


def available_formats() -> List[str]:
    _load_builtins()
    return sorted(_REGISTRY)


_LOADED = False


def _load_builtins() -> None:
    """يستورد المُصيِّرات المدمجة عند أول حاجة.

    الاستيراد كسولٌ عمدًا: ``python-pptx`` قد تكون غائبة، و``lxml``
    ثقيلة. استيرادها عند الإقلاع يُبطئ فتح البرنامج لمن لا يستعمل هذه
    الصيغ أصلًا — وهو الحال الافتراضي.
    """
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    from document.exporters import (  # noqa: F401
        chapters_export,
        html_export,
        pdf_export,
        pptx_export,
        scorm_export,
        study_export,
    )


def write_exports(ctx: ExportContext,
                  formats: List[str]) -> List[Path]:
    """يشغّل المُصيِّرات المطلوبة ويعيد مسارات ما كُتب فعلًا.

    كل مُصيِّر معزول: استثناؤه يُسجَّل ولا يتسرّب. الترتيب محفوظ كما
    طلبه المستخدم، والتكرار يُسقَط.
    """
    _load_builtins()
    written: List[Path] = []
    seen: set[str] = set()
    for raw in formats or []:
        name = (raw or "").strip().lower().lstrip(".")
        if not name or name in seen:
            continue
        seen.add(name)
        exporter = _REGISTRY.get(name)
        if exporter is None:
            logger.warning(f"صيغة إخراج غير معروفة، تُخطّت: {name}")
            continue
        try:
            path = exporter(ctx)
        except Exception as exc:
            # مخرج إضافي لا شرط نجاح — انظر عقد الفشل في مقدّمة الوحدة.
            logger.warning(f"تعذّر إخراج «{name}»: {exc}")
            continue
        if path is not None:
            written.append(Path(path))
    if written:
        logger.info("مخرجات إضافية: " + "، ".join(p.name for p in written))
    return written
