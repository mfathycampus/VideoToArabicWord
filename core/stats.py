"""إحصاءات الاستخدام على الجهاز — ما تعرضه الصفحة الرئيسية.

**المصدر:** سجلٌّ إلحاقي صغير (``jobs.jsonl``) يُكتب له سطرٌ عند نهاية كل
مهمّة مهما كانت نهايتها، ومعه مسحٌ لمجلد الإخراج الحالي يستدرك المهامّ
التي سبقت هذا السجلّ (الإصدارات قبل 1.15) أو كُتبت بمدخلٍ آخر. وسجلّ
التدقيق لا يصلح مصدرًا: معطّل افتراضيًّا، وغرضه الامتثال لا العرض.

**ولا يحمل السجلّ محتوى:** أرقامٌ وتواريخ وامتدادات، واسم المهمّة لعرضه
في «آخر المهامّ» على هذا الجهاز وحده. ما قد يغادر الجهاز إلى مسؤول
الترخيص هو ``to_payload`` — أعدادٌ مجمّعة فقط، بلا أسماء ولا تواريخ مهامّ.

لا شيء هنا يرمي: الإحصاء زينةٌ على البرنامج لا شرطٌ لعمله.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

from utils.logger import logger

LEDGER_FILENAME = "jobs.jsonl"
#: آخر عدد مهامّ يُرسم لها منحنى الجودة.
TREND_LENGTH = 12
#: عدد الأشهر في مخطّط النشاط.
MONTHS = 6
FINAL_STATUSES = ("completed", "failed", "cancelled")


def default_stats_dir() -> Path:
    override = os.environ.get("VTAD_STATS_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".config" / "video_ai_doc" / "stats"


def job_key(job_dir: Path) -> str:
    """معرّفٌ ثابت للمهمّة لا يكشف اسمها ولا مسارها."""
    try:
        text = str(Path(job_dir).resolve())
    except OSError:
        text = str(job_dir)
    return hashlib.sha1(text.lower().encode("utf-8")).hexdigest()[:16]


@dataclass
class JobFact:
    key: str
    at: str                              # ISO محلي
    status: str
    name: str = ""                       # محلّي فقط — لا يُرسل أبدًا
    duration_seconds: float = 0.0
    elapsed_seconds: float = 0.0
    quality_score: Optional[float] = None
    sections: int = 0
    figures: int = 0
    questions: int = 0
    glossary: int = 0
    outputs: List[str] = field(default_factory=list)
    audio_only: bool = False
    rewrite: bool = False
    vision: bool = False
    study: bool = False
    provider: str = ""
    engine: str = ""
    model: str = ""
    profile: str = ""

    @property
    def when(self) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(self.at)
        except (TypeError, ValueError):
            return None


# ── الجمع ───────────────────────────────────────────────────────────
def fact_from_job_dir(job_dir: Path, status: Optional[str] = None) -> Optional[JobFact]:
    """يبني الحقيقة من ملفّات المهمّة المحفوظة (``course._row_from_job``)."""
    from core.course import _row_from_job
    from core.job_registry import read_job

    try:
        summary = read_job(Path(job_dir))
        if summary is None:
            return None
        row = _row_from_job(summary)
    except Exception as exc:                          # noqa: BLE001
        logger.warning(f"تعذّرت قراءة إحصاء المهمّة {job_dir}: {exc}")
        return None
    when = summary.updated_at or datetime.now()
    return JobFact(
        key=job_key(job_dir), at=when.isoformat(timespec="seconds"),
        status=status or summary.status, name=summary.name,
        duration_seconds=round(row.duration_seconds, 1),
        quality_score=row.quality_score, sections=row.sections,
        figures=row.figures, questions=row.questions, glossary=row.glossary,
        outputs=list(row.outputs))


def record_job(config, job_dir: Path, status: str, elapsed_seconds: float,
               audio_only: bool = False, directory: Optional[Path] = None) -> None:
    """يُلحق سطر المهمّة بالسجلّ. يُستدعى من خطّ المعالجة ولا يرمي أبدًا."""
    try:
        fact = fact_from_job_dir(job_dir, status) or JobFact(
            key=job_key(job_dir), at=datetime.now().isoformat(timespec="seconds"),
            status=status, name=Path(job_dir).name)
        fact.at = datetime.now().isoformat(timespec="seconds")
        fact.elapsed_seconds = round(float(elapsed_seconds), 1)
        fact.audio_only = bool(audio_only)
        rewrite = getattr(config, "rewrite", None)
        study = getattr(config, "study", None)
        fact.rewrite = bool(getattr(rewrite, "enabled", False))
        fact.vision = fact.rewrite and bool(getattr(rewrite, "send_images", False))
        fact.provider = str(getattr(rewrite, "provider", "")) if fact.rewrite else ""
        fact.study = bool(getattr(study, "enabled", False))
        whisper = getattr(config, "whisper", None)
        fact.engine = str(getattr(whisper, "engine", ""))
        fact.model = str(getattr(whisper, "model_size", ""))
        fact.profile = str(getattr(getattr(config, "application", None),
                                   "content_profile", ""))
        target = Path(directory or default_stats_dir())
        target.mkdir(parents=True, exist_ok=True)
        with open(target / LEDGER_FILENAME, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(fact), ensure_ascii=False) + "\n")
    except Exception as exc:                          # noqa: BLE001
        logger.warning(f"تعذّرت كتابة إحصاء المهمّة: {exc}")


def _read_ledger(directory: Path) -> List[JobFact]:
    path = Path(directory) / LEDGER_FILENAME
    if not path.is_file():
        return []
    facts: List[JobFact] = []
    known = set(JobFact.__dataclass_fields__)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            data = json.loads(line)
            facts.append(JobFact(**{k: v for k, v in data.items() if k in known}))
        except Exception:                             # noqa: BLE001 - سطر مقطوع
            continue
    return facts


def load_facts(output_dirs: Iterable[Path] = (),
               directory: Optional[Path] = None) -> List[JobFact]:
    """آخر حالةٍ لكل مهمّة: من السجلّ، ثم ما في مجلدات الإخراج ولم يُسجَّل."""
    latest: dict = {}
    for fact in _read_ledger(directory or default_stats_dir()):
        latest[fact.key] = fact                    # الأحدث يغلب
    from core.job_registry import list_jobs
    for output_dir in output_dirs:
        try:
            jobs = list_jobs(Path(output_dir)) if Path(output_dir).is_dir() else []
        except Exception:                             # noqa: BLE001
            jobs = []
        for job in jobs:
            key = job_key(job.job_dir)
            if key in latest:
                continue
            fact = fact_from_job_dir(job.job_dir)
            if fact is not None:
                latest[key] = fact
    return sorted(latest.values(), key=lambda f: f.at)


# ── التجميع ─────────────────────────────────────────────────────────
@dataclass
class MonthBucket:
    month: str             # YYYY-MM
    jobs: int = 0
    video_minutes: float = 0.0


@dataclass
class UsageStats:
    jobs_total: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    unfinished: int = 0
    video_seconds: float = 0.0
    processing_seconds: float = 0.0
    sections: int = 0
    figures: int = 0
    questions: int = 0
    glossary: int = 0
    average_quality: Optional[float] = None
    quality_trend: List[float] = field(default_factory=list)
    formats: Counter = field(default_factory=Counter)
    features: Counter = field(default_factory=Counter)
    months: List[MonthBucket] = field(default_factory=list)
    this_month_jobs: int = 0
    this_month_minutes: float = 0.0
    recordings: int = 0
    recording_seconds: float = 0.0
    first_at: Optional[str] = None
    last_at: Optional[str] = None
    recent: List[JobFact] = field(default_factory=list)

    @property
    def success_rate(self) -> Optional[float]:
        finished = self.completed + self.failed
        return None if not finished else 100.0 * self.completed / finished

    @property
    def video_hours(self) -> float:
        return self.video_seconds / 3600.0

    @property
    def speed_ratio(self) -> Optional[float]:
        """دقائق فيديو تُعالَج في كل دقيقة معالجة."""
        if self.processing_seconds <= 0 or self.video_seconds <= 0:
            return None
        return self.video_seconds / self.processing_seconds

    @property
    def is_empty(self) -> bool:
        return self.jobs_total == 0 and self.recordings == 0


def _month_keys(now: datetime, count: int) -> List[str]:
    keys = []
    year, month = now.year, now.month
    for _ in range(count):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(keys))


def scan_recordings(directory: Optional[Path]) -> tuple:
    """(عدد التسجيلات، مجموع ثوانيها) من ملفّات ``*.recording.json``."""
    if directory is None or not Path(directory).is_dir():
        return 0, 0.0
    count, seconds = 0, 0.0
    for path in Path(directory).glob("*.recording.json"):
        count += 1
        try:
            seconds += float(json.loads(path.read_text(encoding="utf-8"))
                             .get("duration_seconds") or 0.0)
        except Exception:                             # noqa: BLE001
            continue
    return count, seconds


def compute(facts: List[JobFact], recordings_dir: Optional[Path] = None,
            now: Optional[datetime] = None) -> UsageStats:
    now = now or datetime.now()
    stats = UsageStats()
    months = {key: MonthBucket(key) for key in _month_keys(now, MONTHS)}
    this_month = f"{now.year:04d}-{now.month:02d}"
    scores: List[float] = []
    for fact in facts:
        stats.jobs_total += 1
        if fact.status == "completed":
            stats.completed += 1
        elif fact.status == "failed":
            stats.failed += 1
        elif fact.status == "cancelled":
            stats.cancelled += 1
        else:
            stats.unfinished += 1
        when = fact.when
        month = when.strftime("%Y-%m") if when else ""
        if fact.status != "completed":
            continue
        stats.video_seconds += fact.duration_seconds
        if fact.elapsed_seconds > 0 and fact.duration_seconds > 0:
            stats.processing_seconds += fact.elapsed_seconds
        stats.sections += fact.sections
        stats.figures += fact.figures
        stats.questions += fact.questions
        stats.glossary += fact.glossary
        stats.formats.update(fact.outputs)
        for name in ("rewrite", "vision", "study", "audio_only"):
            if getattr(fact, name):
                stats.features[name] += 1
        if fact.quality_score is not None:
            scores.append(float(fact.quality_score))
        if month in months:
            months[month].jobs += 1
            months[month].video_minutes += fact.duration_seconds / 60.0
        if month == this_month:
            stats.this_month_jobs += 1
            stats.this_month_minutes += fact.duration_seconds / 60.0
    if scores:
        stats.average_quality = sum(scores) / len(scores)
        stats.quality_trend = scores[-TREND_LENGTH:]
    stats.months = list(months.values())
    if facts:
        stats.first_at, stats.last_at = facts[0].at, facts[-1].at
        stats.recent = list(reversed(facts[-5:]))
    stats.recordings, stats.recording_seconds = scan_recordings(recordings_dir)
    return stats


def collect(config) -> UsageStats:
    """إحصاءات هذا الجهاز كما تعرضها الواجهة."""
    output_dir = getattr(getattr(config, "application", None), "output_dir", None)
    recording = getattr(config, "recording", None)
    rec_dir = recording.resolved_output_dir() if recording is not None else None
    facts = load_facts([output_dir] if output_dir else [])
    return compute(facts, rec_dir)


def to_payload(stats: UsageStats) -> dict:
    """ما يُرسل لمسؤول الترخيص: أعدادٌ مجمّعة فقط — لا أسماء ولا نصوص."""
    return {
        "jobs_total": stats.jobs_total,
        "completed": stats.completed,
        "failed": stats.failed,
        "cancelled": stats.cancelled,
        "video_minutes": round(stats.video_seconds / 60.0, 1),
        "processing_minutes": round(stats.processing_seconds / 60.0, 1),
        "average_quality": (None if stats.average_quality is None
                            else round(stats.average_quality, 1)),
        "sections": stats.sections,
        "figures": stats.figures,
        "questions": stats.questions,
        "this_month_jobs": stats.this_month_jobs,
        "this_month_minutes": round(stats.this_month_minutes, 1),
        "recordings": stats.recordings,
        "recording_minutes": round(stats.recording_seconds / 60.0, 1),
        "formats": dict(stats.formats),
        "features": dict(stats.features),
        "last_job_day": (stats.last_at or "")[:10],
    }
