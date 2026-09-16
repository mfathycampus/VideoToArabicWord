"""فصول الفيديو — أرخص مخرج في الحزمة وأكثرها إهمالًا.

المُخطِّط يعرف أصلًا أين يبدأ كل قسم (``DocumentSection.start_timestamp``).
لصق هذه القائمة في وصف الفيديو على يوتيوب أو مكتبة المدرسة يحوّل شريط
التقدّم إلى فهرس قابل للنقر — وهي أول ما يستعمله الطالب عمليًّا حين
يعود للمراجعة.

**القواعد ليست تجميلية.** يوتيوب يرفض قائمة الفصول **كلّها** — لا
يتجاهل المخالف منها وحده — إن اختلّ أيٌّ من ثلاثة شروط: أوّل فصل عند
``00:00`` بالضبط، وثلاثة فصول على الأقل، وكل فصل عشر ثوانٍ فأكثر.
ولذلك تُفرض هنا لا تُترك للحظّ: أول فصل يُزاح إلى الصفر، والفصول
القصيرة تُدمج فيما قبلها، وقائمة تعجز عن بلوغ ثلاثة تُلغى صراحةً بدل
كتابة ملفٍّ يرفضه الموقع بلا سبب ظاهر.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from config.schemas import DocumentPlan
from document.exporters import ExportContext, register
from utils.logger import logger

MIN_CHAPTERS = 3
MIN_CHAPTER_SECONDS = 10.0


def _stamp(seconds: float) -> str:
    """‏``M:SS`` تحت الساعة و``H:MM:SS`` فوقها — الصيغة التي يقبلها يوتيوب."""
    total = max(0, int(round(float(seconds))))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def build_chapters(plan: DocumentPlan,
                   duration_seconds: float) -> List[Tuple[float, str]]:
    """يعيد ``[(ثانية, عنوان)]`` مطابقًا لقواعد يوتيوب، أو قائمة فارغة."""
    raw: List[Tuple[float, str]] = []
    for section in plan.sections:
        title = " ".join((section.title or "").split())
        if not title:
            continue
        raw.append((max(0.0, float(section.start_timestamp or 0.0)), title))
    if not raw:
        return []

    raw.sort(key=lambda item: item[0])
    # الشرط الأول: البداية عند الصفر. إزاحة أول فصل أصدق من حقن فصل
    # مُختلَق اسمه «مقدّمة» لا يقابله شيء في المستند.
    raw[0] = (0.0, raw[0][1])

    merged: List[Tuple[float, str]] = [raw[0]]
    for start, title in raw[1:]:
        if start - merged[-1][0] < MIN_CHAPTER_SECONDS:
            continue          # فصل أقصر من الحد: يُدمج فيما قبله
        merged.append((start, title))

    # وآخر فصل كذلك يحتاج مدى كافيًا قبل نهاية الفيديو
    if (len(merged) > 1 and duration_seconds > 0
            and duration_seconds - merged[-1][0] < MIN_CHAPTER_SECONDS):
        merged.pop()

    if len(merged) < MIN_CHAPTERS:
        return []
    return merged


def render_chapters(chapters: List[Tuple[float, str]]) -> str:
    return "\n".join(f"{_stamp(start)} {title}" for start, title in chapters)


def export(ctx: ExportContext) -> Optional[Path]:
    chapters = build_chapters(ctx.plan, ctx.metadata.duration_seconds)
    if not chapters:
        logger.info(
            f"أقسام المستند لا تبلغ {MIN_CHAPTERS} فصول صالحة — "
            "تُخطّى قائمة الفصول (يوتيوب يرفض الأقصر).")
        return None
    path = ctx.sibling(".chapters.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ("# الصق ما تحت هذا السطر في وصف الفيديو لتصير الفصول "
              "قابلة للنقر على شريط التقدّم.\n")
    path.write_text(header + render_chapters(chapters) + "\n",
                    encoding="utf-8")
    return path


register("chapters", export)
