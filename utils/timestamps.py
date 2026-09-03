"""حسابات التوقيت المركزية. الثواني float هي الحقيقة؛ HH:MM:SS عرض فقط."""
from __future__ import annotations


def seconds_to_timestamp(seconds: float) -> str:
    """``HH:MM:SS.mmm`` — للأسماء والسجلات. يتعامل مع القيم السالبة."""
    sign = "-" if seconds < 0 else ""
    seconds = abs(float(seconds))
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{sign}{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def seconds_to_display(seconds: float) -> str:
    """``HH:MM:SS`` — للعرض في المستند."""
    return seconds_to_timestamp(seconds).split(".")[0]


def timestamp_to_seconds(value: str) -> float:
    value = value.strip()
    sign = -1.0 if value.startswith("-") else 1.0
    parts = value.lstrip("-").split(":")
    if len(parts) == 3:
        h, m, s = parts
        return sign * (float(h) * 3600 + float(m) * 60 + float(s))
    if len(parts) == 2:
        m, s = parts
        return sign * (float(m) * 60 + float(s))
    return sign * float(parts[0])


def safe_timestamp_for_filename(seconds: float) -> str:
    """``00-01-23-420`` — صالح كاسم ملف على Windows (لا نقطتان)."""
    return seconds_to_timestamp(seconds).replace(":", "-").replace(".", "-")


def overlap_ratio(start_a: float, end_a: float, start_b: float, end_b: float,
                  method: str = "min") -> float:
    """نسبة التداخل الزمني.

    ``method="min"``: التداخل ÷ أقصر النطاقين — يعطي 1.0 عند احتواء نطاق
    قصير داخل طويل، وهو السلوك المطلوب عمليًا.
    ``method="union"``: تداخل Jaccard الكلاسيكي.
    """
    overlap = min(end_a, end_b) - max(start_a, start_b)
    if overlap <= 0:
        return 0.0
    if method == "min":
        denominator = min(end_a - start_a, end_b - start_b)
    else:
        denominator = max(end_a, end_b) - min(start_a, start_b)
    return overlap / denominator if denominator > 0 else 0.0


def humanize_duration(seconds: float) -> str:
    """مدة مقروءة بالعربية: «3 ساعات و12 دقيقة»."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours} ساعة و{minutes} دقيقة"
    if hours:
        return f"{hours} ساعة"
    if minutes:
        return f"{minutes} دقيقة"
    return f"{seconds} ثانية"


def estimate_remaining(elapsed: float, fraction_done: float) -> float | None:
    """الوقت المتبقي المقدَّر من نسبة الإنجاز والزمن المنقضي.

    يعيد ``None`` قبل تجميع عيّنة كافية — تقدير مبني على أول ثانية من
    العمل يكون عشوائيًا ويفقد ثقة المستخدم أكثر مما يفيده.
    """
    if fraction_done <= 0.005 or elapsed < 5.0:
        return None
    return elapsed * (1.0 - fraction_done) / fraction_done


def strip_source_fingerprint(name: str) -> str:
    """يُزيل بصمة مصدر الملف (``__xxxxxxxx``) من اسم مجلد المهمة للعرض.

    ``core/pipeline.py::job_dir_for`` يُلحق بصمة 8 أحرف سداسية عشرية
    باسم مجلد المهمة لمنع تصادم ملفين بنفس الاسم من مسارين مختلفين.
    البصمة ضرورية على القرص لتمييز المهام، لكنها ضجيج بصري للمستخدم —
    هذه الدالة هي نقطة الفصل بين "اسم المجلد" و"الاسم المعروض".
    """
    import re

    return re.sub(r"__[0-9a-f]{8}\b", "", name).strip()


def humanize_title(stem: str) -> str:
    """يحوّل اسم ملف إلى عنوان مقروء.

    ``WhatsApp Video 2026-08-30 at 10.48.24 AM`` ⇒
    ``WhatsApp Video 2026-08-30``  — تُزال اللواحق التقنية والفواصل.

    بصمة مصدر الملف المُلحَقة باسم مجلد المهمة (انظر
    ``strip_source_fingerprint``) تُزال أولًا فلا تظهر داخل عناوين
    المستندات المدموجة.
    """
    import re

    stem = strip_source_fingerprint(stem)
    text = stem.replace("_", " ")
    # الشرطة فاصلة بين كلمات فقط، لا داخل تاريخ مثل 2026-08-30
    text = re.sub(r"(?<=[^\W\d])-(?=[^\W\d])", " ", text)

    # بصمات المسجّلات الشائعة: تاريخ ووقت لا يصلحان عنوانًا لوثيقة.
    #   "WhatsApp Video 2026-08-30 at 10.48.24 AM" ⇒ "WhatsApp Video"
    #   "Meeting Recording-20260831_110238"        ⇒ "Meeting Recording"
    text = re.sub(r"\s+at\s+\d{1,2}[.:]\d{2}([.:]\d{2})?\s*(AM|PM)?\s*$",
                  "", text, flags=re.I)
    text = re.sub(r"[\s-]*\d{4}-\d{2}-\d{2}([\s-]+\d{2}[.:]?\d{2}([.:]?\d{2})?)?\s*$",
                  "", text)
    text = re.sub(r"[\s-]*\d{8}[_-]\d{6}\s*$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -_.")

    # لواحق تقنية بلا قيمة في عنوان وثيقة
    text = re.sub(r"[\s-]*\b(final|copy|output|export|raw|v\d+)\b\s*$",
                  "", text, flags=re.I).strip(" -_.")
    cleaned = re.sub(r"\s+", " ", text).strip(" -_.")
    # لا نُفرغ العنوان تمامًا: إن لم يبقَ شيء نُعيد الأصل
    return cleaned if len(cleaned) >= 3 else stem


_ARABIC_MONTHS = (
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
)


def arabic_datetime(moment=None) -> str:
    """تاريخ ووقت بصيغة عربية طبيعية: ``31 أغسطس 2026 — 21:33``.

    الصيغة ISO (``2026-08-31``) تنعكس بصريًا في المستندات العربية فتظهر
    ``31-08-2026`` — وهو تاريخ مختلف تمامًا للقارئ. المشكلة في محرّكات
    العرض لا في الملف: مجموعات الأرقام المفصولة بشرطات تُعاد ترتيبها
    مهما ضُبط اتجاه الـ run.

    اسم الشهر بالعربية يحلّها جذريًا: الترتيب «يوم شهر سنة» هو الترتيب
    الطبيعي للقراءة العربية أصلًا، فلا يبقى ما ينعكس.
    """
    from datetime import datetime

    moment = moment or datetime.now()
    month = _ARABIC_MONTHS[moment.month - 1]
    return f"{moment.day} {month} {moment.year} — {moment:%H:%M}"
