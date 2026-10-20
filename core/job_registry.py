"""جرد المهام السابقة في مجلد الإخراج.

الاستئناف مبنيّ ومختبَر منذ الإصدار 1.2، لكن المستخدم لا يصل إليه إلا
بالصدفة: بإعادة اختيار نفس الملف. لا شيء يعرض له ما عالجه، ولا ما توقّف
عند 60%، ولا أين ذهب المستند. هذه الوحدة تقرأ ``job_state.json`` في كل
مجلد مهمة وتعطي الواجهة ما تعرضه.

القراءة فقط: لا تكتب شيئًا ولا تُصلح حالة — التشخيص لا يغيّر ما يشخّصه.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from utils.timestamps import strip_source_fingerprint

STATUS_LABELS = {
    "queued": "في الانتظار",
    "running": "قيد التشغيل",
    "paused": "متوقّفة مؤقتًا",
    "cancel_requested": "طُلب الإلغاء",
    "cancelled": "أُلغيت",
    "completed": "اكتملت",
    "failed": "فشلت",
    "recoverable": "قابلة للاستئناف",
}


@dataclass
class JobSummary:
    job_dir: Path
    name: str
    status: str
    status_label: str
    stage: Optional[str]
    progress: float
    source: Optional[Path]
    document: Optional[Path]
    updated_at: Optional[datetime]
    last_error: Optional[str]
    #: ‏``video`` مهمة معالجة عادية · ``guide`` دليل «سجّلني» (``screenguide``).
    kind: str = "video"
    #: عنوان الدليل كما سمّاه صاحبه (للأدلة وحدها) — به يُفتح للمراجعة.
    guide_title: str = ""

    @property
    def is_guide(self) -> bool:
        return self.kind == "guide"

    @property
    def is_complete(self) -> bool:
        return self.status == "completed"

    @property
    def can_resume(self) -> bool:
        """قابلة للمتابعة: توقّفت قبل الاكتمال ومصدرها ما زال موجودًا."""
        return (not self.is_guide and not self.is_complete
                and self.source is not None and self.source.exists())

    @property
    def can_rebuild(self) -> bool:
        """يمكن إعادة بناء مستندها من الخطة المحفوظة بلا إعادة معالجة.

        الدليل يُعاد إخراجه من نافذة مراجعته (تعديلات الخطوات وأسماء الأنظمة).
        """
        if self.is_guide:
            return (self.job_dir / "guide.json").exists()
        return (self.job_dir / "plan.json").exists()


#: ما يُرتَّب به ما لا وقت له: أقدم من أي مهمة.
_NEVER = datetime.min.replace(tzinfo=timezone.utc)


def sort_key(moment: Optional[datetime]) -> datetime:
    """وقتٌ قابل للمقارنة دائمًا.

    ‏``job_state.json`` يكتب الوقت بمنطقته (UTC)، ووقت الملف بلا منطقة.
    مقارنة النوعين ترفع ``TypeError`` — وفي 1.16.0 أغلق ذلك البرنامج كلّه
    عند «كل المهام…» على جهازٍ فيه دليل «سجّلني» ومهامّ فيديو معًا. الوقت
    بلا منطقة يُعدّ محلّيًّا.
    """
    if moment is None:
        return _NEVER
    if moment.tzinfo is None:
        return moment.astimezone()
    return moment


def _first_existing(job_dir: Path, suffixes: tuple[str, ...]) -> Optional[Path]:
    for candidate in sorted(job_dir.iterdir()):
        if candidate.is_file() and candidate.suffix.lower() in suffixes:
            return candidate
    return None


def read_job(job_dir: Path) -> Optional[JobSummary]:
    """يقرأ مهمة واحدة، أو ``None`` إن لم يكن المجلد مهمة."""
    job_dir = Path(job_dir)
    state_path = job_dir / "job_state.json"
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        # حالة تالفة: نعرضها كمهمة مجهولة بدل إخفائها
        return JobSummary(job_dir=job_dir, name=strip_source_fingerprint(job_dir.name),
                          status="failed",
                          status_label="حالة تالفة", stage=None, progress=0.0,
                          source=None, document=None, updated_at=None,
                          last_error="تعذّرت قراءة job_state.json")

    status = str(state.get("status", "queued"))
    source = state.get("video_path")
    updated = state.get("updated_at")
    try:
        updated_at = datetime.fromisoformat(updated) if updated else None
    except ValueError:
        updated_at = None

    document = None
    reference = (state.get("artifacts") or {}).get("document")
    if reference and (job_dir / reference["filename"]).exists():
        document = job_dir / reference["filename"]
    elif job_dir.exists():
        document = _first_existing(job_dir, (".docx",)) or _first_existing(
            job_dir, (".md", ".txt"))

    return JobSummary(
        job_dir=job_dir,
        name=strip_source_fingerprint(job_dir.name),
        status=status,
        status_label=STATUS_LABELS.get(status, status),
        stage=state.get("stage"),
        progress=float(state.get("progress") or 0.0),
        source=Path(source) if source else None,
        document=document,
        updated_at=updated_at,
        last_error=state.get("last_error"),
    )


def read_guide(guide_dir: Path) -> Optional[JobSummary]:
    """دليل «سجّلني» واحد كمهمة، أو ``None`` إن لم يكن المجلد دليلًا.

    الدليل لا ``job_state.json`` له: يُكتب كاملًا في كل مرة من رحلاته
    المحفوظة، فهو «مكتمل» ما دام مستنده موجودًا.
    """
    guide_dir = Path(guide_dir)
    state_path = guide_dir / "guide.json"
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    title = str(state.get("title") or guide_dir.name)
    journeys = len(state.get("journeys") or [])
    document = _first_existing(guide_dir, (".docx",))
    stamps = [p.stat().st_mtime for p in (state_path, document) if p is not None]
    return JobSummary(
        job_dir=guide_dir,
        name=f"دليل: {title}",
        status="completed" if document else "queued",
        status_label="اكتمل" if document else "بلا مستند بعد",
        stage=f"سجّلني · {journeys} إجراء",
        progress=100.0 if document else 0.0,
        source=None,
        document=document,
        updated_at=(datetime.fromtimestamp(max(stamps)).astimezone()
                    if stamps else None),
        last_error=None,
        kind="guide",
        guide_title=title,
    )


def list_guides(output_dir: Path) -> List[JobSummary]:
    """أدلة «سجّلني» في مجلد الإخراج (``أدلة خطوة بخطوة/``)."""
    from screenguide.guide import guides_root

    root = guides_root(Path(output_dir))
    if not root.is_dir():
        return []
    out: List[JobSummary] = []
    for child in root.iterdir():
        if child.is_dir():
            summary = read_guide(child)
            if summary is not None:
                out.append(summary)
    return out


def list_jobs(output_dir: Path, *, include_guides: bool = False) -> List[JobSummary]:
    """كل المهام في مجلد الإخراج، الأحدث أولًا.

    ``include_guides`` يضيف أدلة «سجّلني» — للوحة «المهام السابقة» وحدها:
    الدمج وفهرس المقرّر والإحصاءات تقرأ مهامّ الفيديو فقط.
    """
    output_dir = Path(output_dir)
    if not output_dir.is_dir():
        return []
    jobs: List[JobSummary] = []
    for child in output_dir.iterdir():
        if not child.is_dir():
            continue
        summary = read_job(child)
        if summary is not None:
            jobs.append(summary)
    try:
        if include_guides:
            jobs.extend(list_guides(output_dir))
    except Exception:                                  # noqa: BLE001
        pass   # دليلٌ تالف لا يُخفي المهام العادية
    jobs.sort(key=lambda j: sort_key(j.updated_at), reverse=True)
    return jobs
