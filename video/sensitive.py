"""ما يتجاوز أسماء الأشخاص: شاشات الدخول، وأرقام الهوية، والروابط، والمسارات.

``video/redact.py`` يموّه **الأسماء**. رُصد على تشغيل حقيقي (1.15.1، تسجيل
49 دقيقة عن Schoology وChalk وTeams) أن ذلك لا يكفي:

* **صفحة تصحيح اختبار** أظهرت صفوف الطلاب بأرقامهم الأكاديمية كاملة
  (``13111819712``) وأسمائهم العربية، وأسماءً لاتينية قرأها OCR مشوّهة
  («knalid aigahtani») فلم تطابق قائمة الأسماء. الرقم الطويل يُقرأ بثبات
  أكبر من الاسم، فهو هنا **مرساة الصفّ**: يُطمس ما حوله في السطر نفسه.
* **أربع شاشات دخول** (Microsoft: «Sign in»، «Stay signed in?»، مفتاح المرور)
  أُرسلت إلى نموذج الرؤية ووُضعت في المستند. شاشة الدخول لا تضيف إلى الدليل
  ما لا تقوله الخطوة («سجّل الدخول بحساب معارف»)، وفيها اقتراحات مدير كلمات
  المرور وحسابات المستخدم — فتُحجب افتراضيًّا (``document.withhold_sensitive_screens``).
* **شريط العنوان** يحمل معرّفات مقرّرات وتقييمات ورموز OAuth
  (``client_id=…&redirect_uri=…``). يبقى اسم النطاق (‏«maarif.schoology.com»
  خطوةٌ تعليمية) ويُطمس ما بعده.
* **مسارات الملفات** (‏``C:\\Users\\<اسم>\\…``) تكشف اسم حساب الجهاز وبنية
  مجلدات المؤسسة.

والاتجاه نفسه كما في ``redact``: الخطأ نحو الطمس أرخص من كشفٍ لا يُستردّ.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from typing import Iterable

#: رقم هوية/أكاديمي/جوال: تسعة أرقام فأكثر في كلمة أغلبها أرقام. التواريخ
#: (‏26/10/18) والأوقات وأرقام الشعب (‏6046704) أقصر من ذلك.
_MIN_ID_DIGITS = 9
#: نصف عرض الشريط المطموس حول الرقم في سطره (بكسل الصورة الأصلية). جدول
#: Schoology: الاسم يبدأ بعد الرقم بنحو 80–150 بكسل، والدرجة قبله بنحو 140.
ID_ROW_HALF_WIDTH_PX = 260
#: رقمٌ مقطوع في عمود أرقام الهوية: أقلّ عدد أرقام، وأقصى انحراف عن العمود.
_MIN_PARTIAL_DIGITS = 5
ID_COLUMN_TOLERANCE_PX = 45

_URL = re.compile(
    # المخطّط، وOCR يُسقط النقطتين كثيرًا: «https//maarif.schoology.com/…»
    r"^(?:[a-z][a-z0-9+.-]*:?/{1,2}|www\.)?"
    r"(?P<host>(?:[a-z0-9-]+\.)+[a-z]{2,})"            # النطاق
    r"(?P<rest>[/?#].*)$", re.I)
_PATH = re.compile(r"(?:^[A-Za-z]:[\\/]|^\\\\|[\\/](?:Users|home|Documents|Desktop)[\\/])",
                   re.I)

#: علامات شاشة الدخول. القويّ منها يكفي وحده؛ «Sign in» وحدها لا تكفي —
#: تظهر زرًّا في رأس كل صفحة تقريبًا.
_LOGIN_STRONG = re.compile(
    r"\bpass(?:word|key|code)\b|stay signed in|forgot (?:your )?password|"
    r"enter (?:your )?password|can'?t access your account|sign-?in options|"
    r"verify your identity|two-?step verification|authenticator app|"
    r"كلمة (?:ال)?مرور|كلمة السر|رمز التحقق|نسيت كلمة",
    re.I)
_LOGIN_WEAK = re.compile(r"\bsign ?in\b|\blog ?in\b|تسجيل الدخول", re.I)
_LOGIN_CONTEXT = re.compile(
    r"\b(?:account|e-?mail|username|user name|phone|skype|microsoft|google|"
    r"no account|create one)\b|البريد|اسم المستخدم|الحساب",
    re.I)

LOGIN = "login"


def classify_screen(text: str) -> list[str]:
    """أنواع المحتوى الحساس في اللقطة كلّها — ``["login"]`` أو ``[]``."""
    text = text or ""
    if _LOGIN_STRONG.search(text):
        return [LOGIN]
    if _LOGIN_WEAK.search(text) and _LOGIN_CONTEXT.search(text):
        # «Sign in» زرًّا في رأس صفحة عادية لا يصحبه «account/email».
        return [LOGIN]
    return []


def _digits(token: str) -> int:
    return sum(ch.isdigit() for ch in token)


def is_identifier(token: str) -> bool:
    token = token.strip("()[]{}.,:;|-—")
    digits = _digits(token)
    return digits >= _MIN_ID_DIGITS and digits >= 0.75 * max(1, len(token))


def _url_tail_box(word: dict) -> dict | None:
    """مستطيل الجزء بعد النطاق — أو ``None`` إن لم يكن الرابط ذا مسار.

    الإحداثيات لكلمةٍ كاملة؛ الجزء يُقدَّر بنسبة الحروف، مع هامش نحو اليسار
    كي لا ينجو أول حرفٍ من المسار.
    """
    text = word["text"].strip("()[]<>\"'")
    match = _URL.match(text)
    if not match or len(match.group("rest")) < 2:
        return None
    start = text.find(match.group("rest"))
    ratio = max(0.0, (start - 1) / max(1, len(text)))
    left = word["left"] + word["width"] * ratio
    return {"left": left, "top": word["top"],
            "width": word["left"] + word["width"] - left, "height": word["height"]}


def find_sensitive_boxes(words: Iterable[dict]
                         ) -> tuple[list[dict], dict[str, int], list[str]]:
    """‏(مستطيلات تُطمس، عدّ حسب النوع، نصوص تُحذف من نصّ الشاشة)."""
    lines: "OrderedDict[tuple, list[dict]]" = OrderedDict()
    for word in words:
        lines.setdefault(word["line"], []).append(word)

    boxes: list[dict] = []
    counts: dict[str, int] = {}
    scrub: list[str] = []

    def add(kind: str, box: dict) -> None:
        boxes.append(box)
        counts[kind] = counts.get(kind, 0) + 1

    # عمود الأرقام: رقمٌ قرأه OCR مقطوعًا («(1311182:» — سبعة أرقام) يُعدّ
    # رقم هوية إن وقع في عمود رقمين كاملين فأكثر. رُصد في صفحة التصحيح:
    # خمسة صفوف من ستة طُمست، ونجا السادس لأن رقمه قُرئ ناقصًا.
    full = [w["left"] + w["width"] / 2 for items in lines.values()
            for w in items if is_identifier(w["text"])]

    def in_id_column(word: dict) -> bool:
        if len(full) < 2 or _digits(word["text"]) < _MIN_PARTIAL_DIGITS:
            return False
        centre = word["left"] + word["width"] / 2
        return sum(1 for c in full if abs(c - centre) <= ID_COLUMN_TOLERANCE_PX) >= 2

    row_bands: list[dict] = []
    for items in lines.values():
        anchors = [w for w in items
                   if is_identifier(w["text"]) or in_id_column(w)]
        for anchor in anchors:
            centre = anchor["left"] + anchor["width"] / 2
            row = [w for w in items
                   if abs((w["left"] + w["width"] / 2) - centre) <= ID_ROW_HALF_WIDTH_PX]
            left = min(w["left"] for w in row)
            right = max(w["left"] + w["width"] for w in row)
            top = min(w["top"] for w in row)
            bottom = max(w["top"] + w["height"] for w in row)
            # الشريط يمتدّ إلى حدّه حتى لو لم يقرأ OCR ما فيه: الاسم المجاور
            # قد لا يُقرأ أصلًا، والرقم وحده يكفي دليلًا على صفّ شخص.
            left = min(left, centre - ID_ROW_HALF_WIDTH_PX)
            right = max(right, centre + ID_ROW_HALF_WIDTH_PX)
            add("id_row", {"left": max(0.0, left), "top": top,
                           "width": right - max(0.0, left), "height": bottom - top})
            scrub.extend(w["text"] for w in row)
        row_bands.extend(boxes[-len(anchors):] if anchors else [])
        for word in items:
            text = word["text"]
            if word in anchors:
                continue
            # الرابط قبل المسار: «maarif.schoology.com/home/recent-activity»
            # فيه «/home/» وهو رابط يبقى نطاقه ظاهرًا.
            tail = _url_tail_box(word)
            if tail is not None:
                add("url", tail)
                scrub.append(text)
            elif _PATH.search(text):
                add("path", word)
                scrub.append(text)
    for band in _unread_rows(row_bands):
        add("id_row", band)
    return boxes, counts, list(dict.fromkeys(scrub))


def _unread_rows(bands: list[dict]) -> list[dict]:
    """صفوفٌ في الجدول نفسه لم يقرأ OCR رقمها — تُستنتج من انتظام الصفوف.

    رُصد في صفحة التصحيح: صفّ طالبٍ نجا لأن OCR لم يقرأ رقمه أصلًا، وآخر
    أعلى الجدول تغطّي القائمةُ المنسدلة رقمَه. جدولٌ بثلاثة صفوف مقروءة
    فأكثر في عمودٍ واحد وبمسافات منتظمة يُكمَل: الفجوة بين صفّين بضعف
    المسافة صفٌّ ناقص، ويُضاف صفٌّ واحد قبل الأول وآخر بعد الأخير.
    """
    if len(bands) < 3:
        return []
    bands = sorted(bands, key=lambda b: b["top"])
    centres = [b["left"] + b["width"] / 2 for b in bands]
    if max(centres) - min(centres) > 2 * ID_COLUMN_TOLERANCE_PX:
        return []
    steps = [b["top"] - a["top"] for a, b in zip(bands, bands[1:])]
    step = sorted(steps)[len(steps) // 2]
    if step <= 0:
        return []
    template = bands[0]

    def band_at(top: float) -> dict:
        return {"left": template["left"], "top": max(0.0, top),
                "width": template["width"], "height": template["height"]}

    extra = [band_at(bands[0]["top"] - step), band_at(bands[-1]["top"] + step)]
    for a, b in zip(bands, bands[1:]):
        gap = b["top"] - a["top"]
        missing = round(gap / step) - 1
        if 1 <= missing <= 3 and abs(gap - (missing + 1) * step) <= 0.25 * step:
            extra.extend(band_at(a["top"] + step * k) for k in range(1, missing + 1))
    return extra


def scrub_screen_text(text: str, tokens: Iterable[str]) -> str:
    """يحذف ما طُمس من نصّ الشاشة، ويقصر كل رابطٍ على نطاقه."""
    for token in sorted(set(tokens), key=len, reverse=True):
        if not token.strip():
            continue
        cleaned = token.strip("()[]<>\"'")
        match = _URL.match(cleaned)
        replacement = match.group("host") if match else ""
        text = text.replace(token, replacement)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if len(line) >= 2)
