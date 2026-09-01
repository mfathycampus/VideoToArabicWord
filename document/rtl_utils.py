"""أدوات ضبط الاتجاه العربي (RTL / Complex Script) في مستندات OOXML.

الخلاصة الهندسية:
    النص العربي في OOXML هو "Complex Script". Word يقرأ خصائصه من عناصر
    مختلفة عن اللاتينية:

        الخط    ← w:rFonts/@w:cs      (وليس @w:ascii أو @w:hAnsi)
        الحجم   ← w:szCs              (وليس w:sz)
        الغامق  ← w:bCs               (وليس w:b)
        المائل  ← w:iCs               (وليس w:i)
        الاتجاه ← w:rtl  داخل rPr

    python-docx يضبط ascii/hAnsi/sz/b فقط، لذلك أي ضبط عبر
    ``run.font.name`` أو ``run.font.size`` **لا يؤثر على النص العربي إطلاقًا**.

    كذلك ترتيب أبناء ``w:pPr`` و ``w:rPr`` مُلزَم بالمخطط (sequence)،
    فلا يجوز استخدام ``append`` — نستخدم ``insert_element_before``.
"""

from __future__ import annotations

import re
from typing import Optional

from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

# ترتيب المخطط: العناصر التي تأتي *بعد* w:bidi داخل w:pPr
_PPR_AFTER_BIDI = (
    "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind",
    "w:contextualSpacing", "w:mirrorIndents", "w:suppressOverlap", "w:jc",
    "w:textDirection", "w:textAlignment", "w:textboxTightWrap",
    "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr",
    "w:pPrChange",
)

# ترتيب المخطط: العناصر التي تأتي *بعد* w:szCs داخل w:rPr
_RPR_AFTER_SZCS = (
    "w:highlight", "w:u", "w:effect", "w:bdr", "w:shd", "w:fitText",
    "w:vertAlign", "w:rtl", "w:cs", "w:em", "w:lang", "w:eastAsianLayout",
    "w:specVanish", "w:oMath",
)

# ترتيب المخطط: العناصر التي تأتي *بعد* w:bidiVisual داخل w:tblPr
_TBLPR_AFTER_BIDIVISUAL = (
    "w:tblStyleRowBandSize", "w:tblStyleColBandSize", "w:tblW", "w:jc",
    "w:tblCellSpacing", "w:tblInd", "w:tblBorders", "w:shd", "w:tblLayout",
    "w:tblCellMar", "w:tblLook", "w:tblCaption", "w:tblDescription",
    "w:tblPrChange",
)

# ترتيب المخطط: العناصر التي تأتي *بعد* w:bidi داخل w:sectPr
_SECTPR_AFTER_BIDI = (
    "w:rtlGutter", "w:docGrid", "w:printerSettings", "w:sectPrChange",
)


def _set_flag(parent, tag: str, successors: tuple[str, ...]) -> None:
    """يضيف عنصر راية boolean في موضعه الصحيح، أو يعيد ضبطه إن وُجد."""
    existing = parent.find(qn(tag))
    if existing is not None:
        existing.set(qn("w:val"), "1")
        return
    element = OxmlElement(tag)
    element.set(qn("w:val"), "1")
    parent.insert_element_before(element, *successors)


def set_paragraph_rtl(
    paragraph,
    alignment: WD_ALIGN_PARAGRAPH = WD_ALIGN_PARAGRAPH.RIGHT,
) -> None:
    """يجعل الفقرة ثنائية الاتجاه (عربية) مع ضبط المحاذاة.

    ``w:bidi`` يجب أن يسبق ``w:jc`` في المخطط، وإلا قد يرفض Word الملف
    أو يُسقط الخاصية بصمت.
    """
    pPr = paragraph._p.get_or_add_pPr()
    _set_flag(pPr, "w:bidi", _PPR_AFTER_BIDI)
    paragraph.alignment = alignment  # يُنشئ w:jc بعد w:bidi تلقائيًا


def style_arabic_run(
    run,
    font_name: str = "Arial",
    size_pt: Optional[float] = None,
    bold: bool = False,
    italic: bool = False,
    color: Optional[RGBColor] = None,
) -> None:
    """يضبط خصائص النص العربي على مساري Latin و Complex Script معًا.

    هذه الدالة هي البديل الإلزامي عن ``run.font.name`` / ``run.font.size``
    في أي نص عربي.
    """
    rPr = run._r.get_or_add_rPr()

    # 1) الخط: ascii/hAnsi للاتينية، cs للعربية
    rFonts = rPr.get_or_add_rFonts()
    rFonts.set(qn("w:ascii"), font_name)
    rFonts.set(qn("w:hAnsi"), font_name)
    rFonts.set(qn("w:cs"), font_name)

    # 2) الحجم: sz للاتينية، szCs للعربية (بوحدة نصف النقطة)
    if size_pt is not None:
        half_points = str(int(round(size_pt * 2)))
        rPr.get_or_add_sz().set(qn("w:val"), half_points)
        existing_szcs = rPr.find(qn("w:szCs"))
        if existing_szcs is None:
            szCs = OxmlElement("w:szCs")
            szCs.set(qn("w:val"), half_points)
            rPr.insert_element_before(szCs, *_RPR_AFTER_SZCS)
        else:
            existing_szcs.set(qn("w:val"), half_points)

    # 3) الغامق/المائل: b + bCs معًا
    run.bold = bold
    run.italic = italic
    if bold:
        rPr.get_or_add_bCs()
    if italic:
        rPr.get_or_add_iCs()

    # 4) اتجاه النص داخل الـ run
    rPr.get_or_add_rtl()

    if color is not None:
        run.font.color.rgb = color


def set_cell_rtl(cell) -> None:
    """يضبط اتجاه خلية الجدول وكل فقراتها."""
    for paragraph in cell.paragraphs:
        set_paragraph_rtl(paragraph)


def set_table_rtl(table) -> None:
    """يعكس ترتيب أعمدة الجدول بصريًا.

    العنصر الصحيح للجداول هو ``w:bidiVisual`` وليس ``w:bidi``.
    """
    tblPr = table._tbl.tblPr
    _set_flag(tblPr, "w:bidiVisual", _TBLPR_AFTER_BIDIVISUAL)
    table.alignment = WD_TABLE_ALIGNMENT.RIGHT
    for row in table.rows:
        for cell in row.cells:
            set_cell_rtl(cell)


def set_section_rtl(section) -> None:
    """يضبط اتجاه القسم بأكمله (يؤثر على الهوامش والتذييل والترقيم)."""
    sectPr = section._sectPr
    _set_flag(sectPr, "w:bidi", _SECTPR_AFTER_BIDI)


def configure_document_defaults(
    document,
    font_name: str = "Arial",
    body_size_pt: float = 13.0,
) -> None:
    """يضبط الخط الافتراضي للمستند كله على مسار Complex Script.

    بدون هذا، أي نص لا يمر عبر ``style_arabic_run`` سيرث خط الثيم.
    يُستدعى مرة واحدة بعد ``Document()`` مباشرة.
    """
    normal = document.styles["Normal"]
    normal.font.name = font_name
    normal.font.size = Pt(body_size_pt)

    rPr = normal.element.get_or_add_rPr()
    rFonts = rPr.get_or_add_rFonts()
    rFonts.set(qn("w:cs"), font_name)
    rFonts.set(qn("w:ascii"), font_name)
    rFonts.set(qn("w:hAnsi"), font_name)

    half_points = str(int(round(body_size_pt * 2)))
    existing_szcs = rPr.find(qn("w:szCs"))
    if existing_szcs is None:
        szCs = OxmlElement("w:szCs")
        szCs.set(qn("w:val"), half_points)
        rPr.insert_element_before(szCs, *_RPR_AFTER_SZCS)
    else:
        existing_szcs.set(qn("w:val"), half_points)

    for section in document.sections:
        set_section_rtl(section)


# محارف العزل الاتجاهي (Unicode Bidi Isolates)
LRI = "\u2066"   # Left-to-Right Isolate
PDI = "\u2069"   # Pop Directional Isolate


def isolate_ltr(text: str) -> str:
    """يعزل نصًا لاتينيًا/رقميًا داخل فقرة عربية.

    بدون هذا العزل، خوارزمية Bidi تعيد ترتيب المقاطع الرقمية حول الفواصل
    عند العرض: ``1280×720`` يظهر ``720×1280``، و ``⏱ 00:01:23`` يظهر
    ``00:01:23 ⏱``. الـ XML صحيح تمامًا — العطل في *العرض* فقط، ولا
    يُكتشف إلا بفتح الملف فعليًا.
    """
    return f"{LRI}{text}{PDI}"


def style_ltr_run(
    run,
    font_name: str = "Arial",
    size_pt: Optional[float] = None,
    bold: bool = False,
    color: Optional[RGBColor] = None,
) -> None:
    """يضبط run يحمل محتوى لاتينيًا/رقميًا داخل فقرة عربية.

    مطابق لـ ``style_arabic_run`` إلا أنه **لا يضيف** ``w:rtl``، لأن
    إضافته تقلب ترتيب المحتوى اللاتيني.
    """
    rPr = run._r.get_or_add_rPr()
    rFonts = rPr.get_or_add_rFonts()
    rFonts.set(qn("w:ascii"), font_name)
    rFonts.set(qn("w:hAnsi"), font_name)
    rFonts.set(qn("w:cs"), font_name)

    if size_pt is not None:
        half_points = str(int(round(size_pt * 2)))
        rPr.get_or_add_sz().set(qn("w:val"), half_points)
        existing = rPr.find(qn("w:szCs"))
        if existing is None:
            szCs = OxmlElement("w:szCs")
            szCs.set(qn("w:val"), half_points)
            rPr.insert_element_before(szCs, *_RPR_AFTER_SZCS)
        else:
            existing.set(qn("w:val"), half_points)

    run.bold = bold
    if bold:
        rPr.get_or_add_bCs()
    if color is not None:
        run.font.color.rgb = color


# نطاقات المحارف ذات الاتجاه القوي من اليمين لليسار
_RTL_RANGES = (
    ("֐", "׿"),   # عبري
    ("؀", "ۿ"),   # عربي
    ("܀", "ݏ"),   # سرياني
    ("ݐ", "ݿ"),   # عربي ملحق
    ("ࢠ", "ࣿ"),   # عربي ممتد-A
    ("יִ", "﷿"),   # أشكال العرض A
    ("ﹰ", "﻿"),   # أشكال العرض B
)


def is_rtl_char(ch: str) -> bool:
    return any(low <= ch <= high for low, high in _RTL_RANGES)


def contains_rtl(text: str) -> bool:
    return any(is_rtl_char(ch) for ch in text)


def dominant_direction(text: str) -> str:
    """اتجاه الفقرة الحاوية للنص: ``"rtl"`` أو ``"ltr"``.

    القاعدة **وجود لا أغلبية**: أي نص فيه حرف عربي واحد يُعامل كنص عربي.

    الأغلبية قاعدة خاطئة هنا. نص مثل
    ``"تفريغ ومحتوى مرئي — static_single_slide.mp4"``
    أغلب حروفه لاتينية، فتصنّفه قاعدة الأغلبية ``ltr`` ويخرج بلا
    ``w:rtl``، فيُعرض جزؤه العربي مكسورًا. أما المقاطع اللاتينية داخله
    فتحميها عزلات الاتجاه (``isolate_mixed``) لا تصنيف الفقرة.

    نص بلا أي حرف قوي (``"01_2026.mp4"``) يُعدّ ``ltr``.
    """
    return "rtl" if contains_rtl(text) else "ltr"


def style_auto_run(
    run,
    text: str,
    font_name: str = "Arial",
    size_pt: Optional[float] = None,
    bold: bool = False,
    color: Optional[RGBColor] = None,
) -> None:
    """يضبط الـ run حسب اتجاه نصه."""
    if dominant_direction(text) == "rtl":
        style_arabic_run(run, font_name, size_pt, bold=bold, color=color)
    else:
        style_ltr_run(run, font_name, size_pt, bold=bold, color=color)


def add_mixed_text(
    paragraph,
    text: str,
    font_name: str = "Arial",
    size_pt: Optional[float] = None,
    bold: bool = False,
    color: Optional[RGBColor] = None,
) -> None:
    """يكتب نصًا مختلطًا في الفقرة كـ runs موجّهة، بلا محارف مضافة.

    البديل الصحيح عن العزل بمحارف يونيكود: Word يرتّب كل run حسب
    اتجاهه، فيظهر ``محاضرة 01.mp4`` سليمًا دون مربّعات.
    """
    for segment, is_arabic in split_by_direction(text):
        run = paragraph.add_run(segment)
        if is_arabic:
            style_arabic_run(run, font_name, size_pt, bold=bold, color=color)
        else:
            style_ltr_run(run, font_name, size_pt, bold=bold, color=color)


# مقاطع لاتينية/رقمية: حروف وأرقام وما يفصلها من نقاط ونقطتين وشرطات
_LATIN_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-/:×+()]*")


def isolate_mixed(text: str) -> str:
    """مُهملة — انظر التحذير أعلاه."""
    return text


def auto_isolate(text: str) -> str:
    """مُهملة: تُعيد النص كما هو. الاتجاه يُضبط على مستوى الفقرة/الـ run."""
    return text


def split_by_direction(text: str) -> list[tuple[str, bool]]:
    """يقسّم نصًا مختلطًا إلى مقاطع ``(نص, عربي؟)``.

    كل مقطع يصير ``run`` مستقلًا: العربي بـ ``w:rtl`` واللاتيني بدونه.
    هذا ما يجعل Word يرتّب كلًّا منهما صحيحًا **بلا أي محرف مضاف**.

    المحارف المحايدة (مسافات وترقيم) تُلحق بالمقطع السابق حفاظًا على
    التماسك البصري.
    """
    if not text:
        return []
    segments: list[tuple[str, bool]] = []
    buffer = ""
    current: Optional[bool] = None
    for char in text:
        if is_rtl_char(char):
            kind: Optional[bool] = True
        elif char.isalpha() or char.isdigit():
            kind = False
        else:
            kind = None          # محايد: يتبع ما قبله
        if kind is None or kind == current:
            buffer += char
            continue
        if buffer:
            segments.append((buffer, bool(current)))
        buffer, current = char, kind
    if buffer:
        segments.append((buffer, bool(current)))
    return segments


def set_paragraph_ltr(paragraph, alignment=None) -> None:
    """يجعل الفقرة LTR صراحةً مع إبقاء المحاذاة المطلوبة.

    تُستخدم للقيم اللاتينية/الرقمية الخالصة (الأبعاد، التوقيت، أسماء
    الملفات الإنجليزية). فقرة LTR تعرض ``1920×1080`` بترتيبها الصحيح،
    بينما الفقرة العربية تعكسها إلى ``1080×1920``.
    """
    pPr = paragraph._p.get_or_add_pPr()
    existing = pPr.find(qn("w:bidi"))
    if existing is not None:
        pPr.remove(existing)
    bidi = OxmlElement("w:bidi")
    bidi.set(qn("w:val"), "0")
    pPr.insert_element_before(bidi, *_PPR_AFTER_BIDI)
    if alignment is not None:
        paragraph.alignment = alignment
