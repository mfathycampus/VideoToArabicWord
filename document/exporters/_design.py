"""هوية بصرية واحدة لكل المخرجات الإضافية (شرائح · ويب · SCORM · دليل).

مخرجٌ يبدو كل صيغةٍ منه من مصمّمٍ مختلف يقول للمعلّم إن البرنامج لا يعرف
ما يصنع. لذلك الألوان والتحويلات المشتركة هنا، لا مكرّرةً في كل مُصيِّر.
"""
from __future__ import annotations

import html
import re

#: ‏LRM: يعزل الأرقام عن سياق الحروف العربية المجاورة.
LRM = "\u200e"
#: WJ: يمنع كسر السطر عند الشرطة داخل «2026-2027» فلا تتوزّع على سطرين.
WJ = "\u2060"

# ---------------------------------------------------------------------
# اللوحة — نيليّ هادئ + كهرمانيّ للتمييز. التباين كلّه يتجاوز 4.5:1 (AA).
# ---------------------------------------------------------------------
NAVY = "1B1C55"        # خلفيات الغلاف والإطار الداكن
BRAND = "2D2E82"       # اللون الأساسي
BRAND_LIGHT = "ECECF7"  # بطاقات وخلفيات فاتحة
ACCENT = "F2A900"      # تمييز: أرقام، أشرطة، شارات
ACCENT_DARK = "B87F00"
INK = "1A1B2E"
MUTED = "5B6070"
LINE = "DDE0EC"
PAPER = "F6F7FB"
WHITE = "FFFFFF"
STAGE = "14152B"       # خلفية شرائح اللقطات: تُبرز لقطة الشاشة الفاتحة

#: أرقام وتوقيتات ونطاقات (2026-2027 · 00:04:43 · 3/11) داخل جملة عربية.
#: خوارزمية Unicode للاتجاه تقلب «2026-2027» إلى «2027-2026» حين يسبقها
#: حرف عربي، لأن الأرقام تُعامَل أرقامًا عربية والشرطة محايدة — عطلٌ يقرؤه
#: الجمهور خطأً في العنوان نفسه.
_NUMERIC_RUN = re.compile(r"\d+(?:[-–/:.]\d+)+")


def bidi_safe(text: str) -> str:
    """يحيط كل نطاق رقمي بـ LRM فيبقى بترتيبه مهما جاوره من حروف."""
    if not text:
        return ""
    def wrap(match):
        run = match.group(0).replace("-", f"{WJ}-{WJ}").replace("–", f"{WJ}–{WJ}")
        return f"{LRM}{run}{LRM}"
    return _NUMERIC_RUN.sub(wrap, text)


def esc(text: str) -> str:
    """هروب HTML مع حماية اتجاه الأرقام."""
    return html.escape(bidi_safe(text or ""), quote=True)


def is_timestamp_only(text: str) -> bool:
    """تسمية لقطة لا تحمل إلا توقيتًا («00:04:43») — ليست تسمية."""
    return bool(re.fullmatch(r"[\s\d:.\-–—]*", text or ""))


def clean_caption(text: str) -> str:
    text = " ".join((text or "").split())
    return "" if is_timestamp_only(text) else text


def fit(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    # لا يُقطع وسط كلمة إن وُجد فراغ قريب
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ،,;:-") + "…"


_STEP_PREFIX = re.compile(r"^\s*(\d+)\s*[.)\-–]\s*")


def split_step(text: str):
    """``("3", "اضغط حفظ")`` من «3. اضغط حفظ»، وإلا ``(None, text)``."""
    match = _STEP_PREFIX.match(text or "")
    if not match:
        return None, (text or "").strip()
    return match.group(1), text[match.end():].strip()


# ---------------------------------------------------------------------
# الأعداد العربية: «3 لقطة» خطأ نحوي يراه كل قارئ، والصواب «3 لقطات».
# ---------------------------------------------------------------------
#: ‏(مفرد، مثنّى، جمع للعدد 3–10، تمييز للعدد 11 فما فوق)
_FORMS = {
    "figure": ("لقطة", "لقطتان", "لقطات", "لقطة"),
    "section": ("قسم", "قسمان", "أقسام", "قسمًا"),
    "objective": ("هدف", "هدفان", "أهداف", "هدفًا"),
    "question": ("سؤال", "سؤالان", "أسئلة", "سؤالًا"),
    "term": ("مصطلح", "مصطلحان", "مصطلحات", "مصطلحًا"),
    "card": ("بطاقة", "بطاقتان", "بطاقات", "بطاقة"),
}


def ar_noun(n: int, kind: str) -> str:
    """الاسم بصيغته الصحيحة للعدد ``n`` (دون العدد نفسه)."""
    one, two, few, many = _FORMS[kind]
    if n == 1:
        return one
    if n == 2:
        return two
    return few if 3 <= n <= 10 else many


def ar_count(n: int, kind: str) -> str:
    """«قسم واحد» · «قسمان» · «5 أقسام» · «12 قسمًا»."""
    if n == 1:
        return f"{_FORMS[kind][0]} واحد" if kind in ("section", "objective", "question", "term") else f"{_FORMS[kind][0]} واحدة"
    if n == 2:
        return ar_noun(2, kind)
    return f"{n} {ar_noun(n, kind)}"
