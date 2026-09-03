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
from datetime import datetime
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

    @property
    def is_complete(self) -> bool:
        return self.status == "completed"

    @property
    def can_resume(self) -> bool:
        """قابلة للمتابعة: توقّفت قبل الاكتمال ومصدرها ما زال موجودًا."""
        return (not self.is_complete
                and self.source is not None and self.source.exists())

    @property
    def can_rebuild(self) -> bool:
        """يمكن إعادة بناء مستندها من الخطة المحفوظة بلا إعادة معالجة."""
        return (self.job_dir / "plan.json").exists()


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


def list_jobs(output_dir: Path) -> List[JobSummary]:
    """كل المهام في مجلد الإخراج، الأحدث أولًا."""
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
    jobs.sort(key=lambda j: (j.updated_at or datetime.min), reverse=True)
    return jobs
