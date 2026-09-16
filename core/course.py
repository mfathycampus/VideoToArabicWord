"""تقرير المقرّر — من مجلد مهامّ إلى صورة واحدة عمّا أُنجز وما ينقص.

**المشكلة تظهر عند الملفّ العشرين لا الأول.** المعلّم الذي عالج مقرّرًا
كاملًا يملك عشرين مجلد مهمّة متشابه الأسماء، وليس عنده وسيلة ليعرف: أي
محاضرة لم تكتمل؟ أيّها بلا حزمة تعليمية؟ أيّها خرج بجودة قراءة منخفضة
ويستحقّ إعادة بناء؟ الجواب اليوم يحتاج فتح عشرين مجلدًا.

**والبيانات كلّها موجودة ولا تُقرأ معًا:** ``job_state.json`` يقول الحالة
والمرحلة، و``metadata.json`` المدّة، و``plan.json`` درجة قابلية القراءة
وتحذيرات بوابة الجودة، و``study.json`` عدد الأسئلة. هذه الوحدة تقرؤها
جميعًا وتُخرج صفًّا لكل محاضرة و**تقرير تغطية** يقول لكل نقصٍ أمرَ
الإصلاح الذي يزيله.

**ولا تعالج شيئًا ولا تغيّر شيئًا** — قراءة محضة. تقريرٌ يُصلح ما يجده
من تلقاء نفسه يصير خطِرًا: يكفي أن يُشغَّل على مجلد خاطئ.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from core.job_registry import JobSummary, list_jobs
from utils.logger import logger

#: ``الامتداد: (التسمية، أمر الإصلاح)`` — ما نتوقّع وجوده بجوار كل مستند.
#: الأمر لا التسمية هو المهمّ: تقريرٌ يقول «ينقص كذا» بلا أن يقول كيف
#: يُستدرَك يترك القارئ حيث وجده.
EXPECTED_OUTPUTS: Dict[str, tuple] = {
    ".docx": ("مستند Word", ""),
    ".html": ("صفحة تفاعلية", "--export html"),
    ".pdf": ("ملف PDF", "--export pdf"),
    ".pptx": ("شرائح", "--export pptx"),
    ".srt": ("ترجمة", ""),
    ".scorm.zip": ("حزمة SCORM", "--export scorm"),
    ".study.html": ("دليل مذاكرة", "--study"),
    ".flashcards.csv": ("بطاقات", "--study"),
}


@dataclass
class LectureRow:
    """صفّ محاضرة واحدة في التقرير."""
    name: str
    job_dir: Path
    status: str
    status_label: str
    progress: float = 0.0
    duration_seconds: float = 0.0
    sections: int = 0
    figures: int = 0
    quality_score: Optional[float] = None
    quality_warnings: List[str] = field(default_factory=list)
    questions: int = 0
    glossary: int = 0
    outputs: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    last_error: str = ""

    @property
    def is_complete(self) -> bool:
        return self.status == "completed"


@dataclass
class CourseReport:
    title: str = ""
    output_dir: Path = Path(".")
    lectures: List[LectureRow] = field(default_factory=list)
    #: ``أمر الإصلاح: عدد المحاضرات التي ينقصها`` — أساس تقرير التغطية.
    remedies: Dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.lectures)

    @property
    def completed(self) -> int:
        return sum(1 for row in self.lectures if row.is_complete)

    @property
    def incomplete(self) -> List[LectureRow]:
        return [row for row in self.lectures if not row.is_complete]

    @property
    def total_seconds(self) -> float:
        return sum(row.duration_seconds for row in self.lectures)

    @property
    def total_questions(self) -> int:
        return sum(row.questions for row in self.lectures)

    @property
    def average_quality(self) -> Optional[float]:
        scores = [row.quality_score for row in self.lectures
                  if row.quality_score is not None]
        return sum(scores) / len(scores) if scores else None


def _read_json(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"تعذّرت قراءة {path.name}: {exc}")
        return None


def _present_outputs(job_dir: Path) -> List[str]:
    """الامتدادات الموجودة فعلًا. تُطابَق من الأطول لتفوز المركّبة.

    ``.study.html`` و``.html`` كلاهما ينتهي بـ``.html``؛ الترتيب بالطول
    يمنع احتساب دليل المذاكرة صفحةً تفاعلية.
    """
    found: List[str] = []
    names = [path.name for path in job_dir.iterdir() if path.is_file()]
    for suffix in sorted(EXPECTED_OUTPUTS, key=len, reverse=True):
        matched = [name for name in names if name.endswith(suffix)]
        if matched:
            found.append(suffix)
            names = [name for name in names if name not in matched]
    return found


def _row_from_job(job: JobSummary) -> LectureRow:
    row = LectureRow(
        name=job.name, job_dir=job.job_dir, status=job.status,
        status_label=job.status_label, progress=job.progress,
        last_error=job.last_error or "")

    metadata = _read_json(job.job_dir / "metadata.json") or {}
    row.duration_seconds = float(metadata.get("duration_seconds") or 0.0)

    plan = _read_json(job.job_dir / "plan.json") or {}
    sections = plan.get("sections") or []
    row.sections = len(sections)
    row.figures = sum(1 for section in sections
                      for block in (section.get("blocks") or [])
                      if block.get("kind") == "figure")
    score = plan.get("quality_score")
    row.quality_score = float(score) if score is not None else None
    row.quality_warnings = [str(w) for w in (plan.get("quality_warnings") or [])]

    study = _read_json(job.job_dir / "study.json") or {}
    row.questions = len(study.get("questions") or [])
    row.glossary = len(study.get("glossary") or [])

    row.outputs = _present_outputs(job.job_dir)
    # النقص يُحسب للمكتملة وحدها: محاضرةٌ توقّفت في التفريغ ينقصها كل
    # شيء، وإدراج ذلك في التغطية يُغرق التقرير بضجيج نعرف سببه أصلًا.
    if row.is_complete:
        row.missing = [s for s in EXPECTED_OUTPUTS if s not in row.outputs]
    return row


def build_report(output_dir: Path, title: str = "") -> CourseReport:
    """يقرأ كل مهامّ مجلد الإخراج ويبني التقرير. لا يعالج ولا يغيّر شيئًا."""
    output_dir = Path(output_dir)
    report = CourseReport(title=title or output_dir.name,
                          output_dir=output_dir)

    jobs = list_jobs(output_dir)
    if not jobs:
        logger.info(f"لا مهامّ في {output_dir}")
        return report

    # الأقدم أولًا: ترتيب المقرّر زمنيّ، و``list_jobs`` يعيد الأحدث أولًا
    # لأن غرضها لوحة «المهام السابقة» لا الفهرس.
    #
    # والاسم فاصلٌ عند تساوي الوقت: دفعةٌ تُعالَج آليًّا تكتب حالاتها في
    # الثانية نفسها، فترتيبٌ يعتمد على الوقت وحده يتبدّل بين تشغيلين
    # ويجعل «المحاضرة الثالثة» تظهر أولًا بلا سبب.
    jobs.sort(key=lambda job: (job.updated_at or datetime.min, job.name))
    report.lectures = [_row_from_job(job) for job in jobs]

    for row in report.lectures:
        if not row.is_complete:
            key = ("تابع المهمّة من «المهام السابقة…»" if row.status != "failed"
                   else "أعد تشغيل الملفّ — راجع الخطأ")
            report.remedies[key] = report.remedies.get(key, 0) + 1
            continue
        # العدد عدد **محاضرات** لا عدد مخرجات ناقصة: أمرٌ واحد
        # (``--study``) يُخرج الدليل والبطاقات معًا، فاحتسابه مرّتين
        # يقول لقارئ التقرير إن محاضرتين تنقصانه وهي واحدة.
        for remedy in {EXPECTED_OUTPUTS[s][1] for s in row.missing
                       if EXPECTED_OUTPUTS[s][1]}:
            report.remedies[remedy] = report.remedies.get(remedy, 0) + 1
    return report
