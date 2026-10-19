"""صيغ العدد العربية في الواجهة — «4 مستندات» لا «4 مستند».

العدد العربي يغيّر معدوده: المفرد للواحد، والمثنّى للاثنين، والجمع
من ثلاثة إلى عشرة، والمفرد المنصوب فوقها («11 مستندًا»، «89 دقيقة»).
والكسر («2.5») يأخذ المفرد. رُصد في لوحة الإحصاءات: «4 مستند»،
«3 دقيقة»، «1526 يومًا» بجانب «89 دقيقة».
"""
from __future__ import annotations

from typing import Tuple

Forms = Tuple[str, str, str, str]

DOCUMENTS: Forms = ("مستند واحد", "مستندان", "مستندات", "مستندًا")
MINUTES: Forms = ("دقيقة واحدة", "دقيقتان", "دقائق", "دقيقة")
TASKS: Forms = ("مهمّة واحدة", "مهمّتان", "مهامّ", "مهمّة")
#: بعد حرف جرّ («من مهمّتين»): المثنّى مجرور.
TASKS_AFTER_PREPOSITION: Forms = ("مهمّة واحدة", "مهمّتين", "مهامّ", "مهمّة")
DAYS: Forms = ("يوم واحد", "يومان", "أيام", "يومًا")


def _num(value: float, digits: int = 0) -> str:
    return f"{value:,.{digits}f}" if digits else f"{int(round(value)):,}"


def count_label(value: float, forms: Forms) -> str:
    one, two, few, many = forms
    if value != int(value):
        return f"{_num(value, 1)} {many[:-2] if many.endswith('ًا') else many}"
    n = int(value)
    if n == 1:
        return one
    if n == 2:
        return two
    tail = n % 100
    return f"{_num(n)} {few if 3 <= tail <= 10 else many}"


def minutes_label(seconds: float) -> str:
    """دقائق بخانة عشرية تحت العشر: «2.4» و«2.7» لا «2» و«3» لشيءٍ واحد."""
    minutes = seconds / 60.0
    return count_label(round(minutes, 1) if minutes < 10 else round(minutes), MINUTES)
