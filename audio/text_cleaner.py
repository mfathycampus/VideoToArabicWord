"""طبقة تنظيف النص العربي — الطبقة التي وعد بها ADR-005 ولم تُنفَّذ.

في الحزمة الأصلية ``text_clean = text_raw`` في كل موضع، أي أن العقد
موجود والتنفيذ غائب. النص الخام يبقى محفوظًا كما هو (ADR-005)، والتنظيف
يُنتج حقلًا موازيًا.
"""

from __future__ import annotations

import re
import unicodedata

_TATWEEL = "ـ"
_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٟۖ-ۭ]")
_MULTISPACE = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([،.؛؟!:])")
_REPEATED_PHRASE = re.compile(r"\b(.{4,40}?)(?:\s+\1\b){2,}")


def normalize_arabic(text: str, strip_diacritics: bool = False) -> str:
    """تطبيع يونيكودي محافظ: لا يغيّر كلام المتحدث، يوحّد الترميز فقط.

    لا نوحّد الألف (أ/إ/آ→ا) ولا التاء المربوطة، لأن ذلك **يغيّر النص
    المعروض** ويخالف "لا تعيد صياغة كلام المتحدث". التوحيد الاختزالي
    مكانه فهرس البحث، لا المستند.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace(_TATWEEL, "")
    if strip_diacritics:
        text = _DIACRITICS.sub("", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    return _MULTISPACE.sub(" ", text).strip()


def collapse_hallucinated_repeats(text: str) -> str:
    """يطوي التكرار الهلوسي الذي ينتجه Whisper على فترات الصمت.

    مثال حقيقي شائع في العربية:
        "شكرا لكم شكرا لكم شكرا لكم شكرا لكم" → "شكرا لكم"
    يُطبَّق فقط على 3 تكرارات فأكثر لتجنّب حذف تكرار بلاغي مقصود.
    """
    previous = None
    while previous != text:
        previous = text
        text = _REPEATED_PHRASE.sub(r"\1", text)
    return text


def clean_segment_text(text: str) -> str:
    return collapse_hallucinated_repeats(normalize_arabic(text))
