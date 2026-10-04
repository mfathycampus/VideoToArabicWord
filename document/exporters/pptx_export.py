"""شرائح من الخطة نفسها — عرضٌ للمعلّم لا نسخةٌ ثانية من المستند.

المستند للقراءة، والشرائح للعرض؛ وما يصلح لأحدهما يقتل الآخر. لذلك لا
تُنسخ الفقرات هنا: كل قسمٍ يصير شريحةً واحدة بعنوانه ونقاطه المكثّفة،
وكل لقطةٍ شاشة تصير شريحةً كاملة بتسميتها. النصّ الكامل يبقى حيث مكانه
الطبيعي — في ملاحظات المتحدّث أسفل الشريحة، يقرؤها هو ولا تُعرض.

**تصميمٌ لا قالبٌ افتراضي.** كل شريحة تُرسم بأشكالٍ محسوبة المقاسات
(غلاف داكن، فهرس، بطاقات نقاط، شرائح لقطات على خلفية داكنة تُبرز لقطة
الشاشة الفاتحة). عنوان كل شريحة يبقى **عنصرَ العنوان الحقيقيّ** في
القالب، فيعمل مخطّط الشرائح وقارئ الشاشة وتصدير PDF كما يتوقّعون.

**‏python-pptx اعتماد اختياري.** غيابها يتخطّى هذه الصيغة وحدها بسطر
في السجلّ ولا يمسّ شيئًا آخر — نفس عقد بقية المُصيِّرات.

**اتجاه النصّ يُضبط بـ XML مباشرةً.** ‏python-pptx لا تعرض خاصيّة
لاتجاه الفقرة إطلاقًا، والشريحة العربية بلا ``rtl="1"`` تخرج بعلامات
الترقيم في أوّل السطر بدل آخره. والخطّ العربي يُحدَّد في ``a:cs`` لا
``a:latin`` وحدها، وإلا رجع النصّ العربي إلى خطّ القالب.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import List, Optional, Tuple

from config.schemas import DocumentSection
from document.alt_text import describe
from document.exporters import ExportContext, register
from document.exporters import _design as D
from utils.logger import logger
from utils.timestamps import seconds_to_display


def _install_hint() -> str:
    """الأمر بالمفسّر الذي يشغّل البرنامج فعلًا — لا ‏«pip» أيًّا كان.

    ‏«pip install» يثبّت في أول Python على PATH، وقد لا يكون الذي يشغّل
    الواجهة؛ فيقول المستخدم «مثبَّتة» ويبقى التحذير.
    """
    import sys
    return ("‏python-pptx غير مثبَّتة في مفسّر البرنامج — صيغة الشرائح "
            f'تحتاجها. ثبّتها عبر: "{sys.executable}" -m pip install python-pptx')


INSTALL_HINT = _install_hint()

#: شريحة تتجاوز هذا العدد من النقاط تصير صفحةً تُقرأ لا شريحةً تُعرض.
MAX_BULLETS = 6
MAX_BULLET_CHARS = 120
#: العنوان الطويل يلتفّ على ثلاثة أسطر فيأكل الشريحة
MAX_TITLE_CHARS = 70
#: أقصى عدد أقسام في شريحة فهرس واحدة (٣ أعمدة × ٨).
AGENDA_PER_SLIDE = 24
AGENDA_ROWS = 8

SLIDE_W, SLIDE_H = 13.333, 7.5
MARGIN = 0.65

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?؟…])\s+")


# ---------------------------------------------------------------------
# محتوى الشرائح (بلا اعتماد على python-pptx — قابل للاختبار وحده)
# ---------------------------------------------------------------------
def _fit(text: str, limit: int) -> str:
    return D.fit(text, limit)


def section_bullets(section: DocumentSection) -> List[str]:
    """نقاط الشريحة: ما كتبته إعادة الصياغة إن وُجد، وإلا يُشتقّ من النصّ.

    ترتيب الأفضلية مقصود. خطةُ الذكاء الاصطناعي تحمل ``bullets`` مكتوبة
    للعرض أصلًا، فهي أولى. وفي المسار الافتراضي (بلا نموذج لغوي) لا
    يوجد إلا كلامٌ منطوق متّصل، فتُؤخذ أوائل الجُمل — وهي في المحاضرة
    عادةً موضع طرح الفكرة قبل شرحها.
    """
    explicit: List[str] = []
    for block in section.blocks:
        if block.kind == "bullets":
            explicit.extend(b for b in block.bullets if (b or "").strip())
    if explicit:
        return [_fit(b, MAX_BULLET_CHARS) for b in explicit[:MAX_BULLETS]]

    # خطوات الدليل الإجرائي نقاطٌ جاهزة للعرض بترتيبها.
    steps = [f"{n}. {(b.text or '').strip()}" for n, b in enumerate(
        (b for b in section.blocks if b.kind == "step" and (b.text or "").strip()),
        start=1)]
    if steps:
        return [_fit(t, MAX_BULLET_CHARS) for t in steps[:MAX_BULLETS]]

    sentences: List[str] = []
    for block in section.blocks:
        if block.kind != "paragraph":
            continue
        for sentence in _SENTENCE_SPLIT.split(block.text or ""):
            sentence = sentence.strip()
            # الجملة الأقصر من ثلاثين حرفًا في كلامٍ منطوق شبه دائمًا
            # حشوٌ («طيب»، «تمام يا شباب») لا فكرة.
            if len(sentence) >= 30:
                sentences.append(_fit(sentence, MAX_BULLET_CHARS))
            if len(sentences) >= MAX_BULLETS:
                return sentences
    return sentences


def _speaker_notes(section: DocumentSection) -> str:
    return "\n\n".join(
        (block.text or "").strip()
        for block in section.blocks
        if block.kind in ("paragraph", "step", "note") and (block.text or "").strip())


def agenda_pages(titles: List[str]) -> List[List[Tuple[int, str]]]:
    """يقسّم عناوين الأقسام على شرائح فهرس بترقيمها الأصلي."""
    numbered = list(enumerate(titles, start=1))
    return [numbered[i:i + AGENDA_PER_SLIDE]
            for i in range(0, len(numbered), AGENDA_PER_SLIDE)]


def _lines_needed(text: str, width_in: float, size_pt: float) -> int:
    """تقدير عدد الأسطر. العربية ≈ 0.0066 بوصة لكل حرف لكل نقطة خطّ."""
    per_line = max(8, int(width_in / (size_pt * 0.0066)))
    return max(1, math.ceil(len(text) / per_line))


# ---------------------------------------------------------------------
# الرسم
# ---------------------------------------------------------------------
class _Deck:
    """يحمل العرض وأدوات الرسم المشتركة بين كل أنواع الشرائح."""

    def __init__(self, presentation, font: str, course_title: str):
        from pptx.util import Inches
        self.prs = presentation
        self.font = font
        self.course_title = course_title
        self.count = 0
        self.inches = Inches
        # «Title Only» — يبقى للشريحة عنوانٌ حقيقيّ في المخطّط
        self.layout = presentation.slide_layouts[5]

    # -- أدوات أولية --------------------------------------------------
    def _rgb(self, hex_color: str):
        from pptx.dml.color import RGBColor
        return RGBColor.from_string(hex_color)

    def new_slide(self, background: str):
        slide = self.prs.slides.add_slide(self.layout)
        self.count += 1
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = self._rgb(background)
        return slide

    def box(self, slide, x, y, w, h, fill: Optional[str] = None,
            line: Optional[str] = None, line_w: float = 1.0,
            radius: Optional[float] = None, oval: bool = False):
        from pptx.enum.shapes import MSO_SHAPE
        from pptx.util import Pt
        kind = (MSO_SHAPE.OVAL if oval else
                MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE)
        shape = slide.shapes.add_shape(
            kind, self.inches(x), self.inches(y), self.inches(w), self.inches(h))
        if radius and not oval:
            shape.adjustments[0] = min(0.5, radius / max(0.01, min(w, h)))
        if fill:
            shape.fill.solid()
            shape.fill.fore_color.rgb = self._rgb(fill)
        else:
            shape.fill.background()
        if line:
            shape.line.color.rgb = self._rgb(line)
            shape.line.width = Pt(line_w)
        else:
            shape.line.fill.background()
        shape.shadow.inherit = False
        return shape

    def _style_run(self, run, size: float, color: str, bold: bool = False):
        from pptx.oxml.ns import qn
        from pptx.util import Pt
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = self._rgb(color)
        run.font.name = self.font
        r_pr = run._r.get_or_add_rPr()
        r_pr.set("lang", "ar-SA")
        if r_pr.find(qn("a:cs")) is None:
            cs = r_pr.makeelement(qn("a:cs"), {"typeface": self.font})
            latin = r_pr.find(qn("a:latin"))
            if latin is not None:
                latin.addnext(cs)
            else:
                r_pr.append(cs)

    def fill_text(self, shape, text: str, size: float, color: str,
                  bold: bool = False, align: str = "r", anchor: str = "t",
                  spacing: float = 1.15, margins=(0, 0, 0, 0)):
        """يكتب نصًّا في شكل أو مربّع نصّ: عربي، يمين، بلا تقليص تلقائي."""
        from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE
        frame = shape.text_frame
        frame.word_wrap = True
        frame.auto_size = MSO_AUTO_SIZE.NONE
        frame.vertical_anchor = {"t": MSO_ANCHOR.TOP, "m": MSO_ANCHOR.MIDDLE,
                                 "b": MSO_ANCHOR.BOTTOM}[anchor]
        left, top, right, bottom = margins
        frame.margin_left, frame.margin_top = self.inches(left), self.inches(top)
        frame.margin_right, frame.margin_bottom = self.inches(right), self.inches(bottom)
        paragraph = frame.paragraphs[0]
        for extra in list(frame.paragraphs)[1:]:
            extra._p.getparent().remove(extra._p)
        for run in list(paragraph.runs):
            run._r.getparent().remove(run._r)
        p_pr = paragraph._p.get_or_add_pPr()
        p_pr.set("rtl", "1")
        p_pr.set("algn", {"r": "r", "l": "l", "c": "ctr"}[align])
        paragraph.line_spacing = spacing
        run = paragraph.add_run()
        run.text = D.bidi_safe(text)
        self._style_run(run, size, color, bold)

    def text(self, slide, x, y, w, h, text: str, size: float, color: str,
             **kwargs):
        shape = slide.shapes.add_textbox(
            self.inches(x), self.inches(y), self.inches(w), self.inches(h))
        self.fill_text(shape, text, size, color, **kwargs)
        return shape

    def title(self, slide, text: str, x, y, w, h, size: float, color: str,
              anchor: str = "m"):
        """عنوان الشريحة في عنصر العنوان الحقيقيّ للقالب."""
        shape = slide.shapes.title
        shape.left, shape.top = self.inches(x), self.inches(y)
        shape.width, shape.height = self.inches(w), self.inches(h)
        self.fill_text(shape, _fit(text, MAX_TITLE_CHARS), size, color,
                       bold=True, anchor=anchor, spacing=1.05)
        # عنصر العنوان يولد أول الشجرة فيقع تحت كل ما يُرسم بعده؛ ننقله إلى
        # القمّة كي لا تغطّيه الزخارف.
        element = shape._element
        element.getparent().append(element)
        return shape

    def footer(self, slide, dark: bool = False):
        muted = "9A9DC8" if dark else D.MUTED
        line = "2E3070" if dark else D.LINE
        self.box(slide, MARGIN, SLIDE_H - 0.52, SLIDE_W - 2 * MARGIN, 0.012,
                 fill=line)
        self.text(slide, SLIDE_W / 2, SLIDE_H - 0.44, SLIDE_W / 2 - MARGIN, 0.3,
                  _fit(self.course_title, 70), 11, muted, anchor="m")
        self.text(slide, MARGIN, SLIDE_H - 0.44, 1.2, 0.3, str(self.count),
                  11, muted, align="l", anchor="m")

    def picture(self, slide, image_path: Path, x, y, w, h, alt: str = "",
                frame: Optional[str] = None, radius: float = 0.12):
        """صورة محتواة بنسبتها الأصلية داخل الصندوق (x, y, w, h) ومتوسّطة فيه."""
        from PIL import Image
        from pptx.enum.shapes import MSO_SHAPE
        from pptx.util import Pt
        with Image.open(image_path) as handle:
            width_px, height_px = handle.size
        scale = min(w / width_px, h / height_px)
        draw_w, draw_h = width_px * scale, height_px * scale
        left, top = x + (w - draw_w) / 2, y + (h - draw_h) / 2
        picture = slide.shapes.add_picture(
            str(image_path), self.inches(left), self.inches(top),
            width=self.inches(draw_w), height=self.inches(draw_h))
        picture.auto_shape_type = MSO_SHAPE.ROUNDED_RECTANGLE
        # ‏Picture لا تعرض ``adjustments`` — نصف القطر يُكتب في XML مباشرةً
        from pptx.oxml.ns import qn
        geometry = picture._element.spPr.find(qn("a:prstGeom"))
        av_list = geometry.find(qn("a:avLst"))
        if av_list is None:
            av_list = geometry.makeelement(qn("a:avLst"), {})
            geometry.append(av_list)
        ratio = min(0.5, radius / max(0.01, min(draw_w, draw_h)))
        av_list.append(av_list.makeelement(
            qn("a:gd"), {"name": "adj", "fmla": f"val {int(ratio * 100000)}"}))
        if frame:
            picture.line.color.rgb = self._rgb(frame)
            picture.line.width = Pt(2.25)
        if alt:
            # PowerPoint يعرض «لا يوجد نص بديل» في مدقّق إمكانية الوصول
            # المدمج، تمامًا كـWord. والموضع هنا ``p:cNvPr/@descr``.
            try:
                picture._element._nvXxPr.cNvPr.set("descr", alt)
            except Exception:
                pass
        return picture, (left, top, draw_w, draw_h)

    def chip(self, slide, x, y, w, h, text: str, fill: str, color: str,
             size: float = 12, bold: bool = True):
        shape = self.box(slide, x, y, w, h, fill=fill, radius=h / 2)
        self.fill_text(shape, text, size, color, bold=bold, align="c", anchor="m")
        return shape

    def notes(self, slide, text: str):
        if text:
            slide.notes_slide.notes_text_frame.text = text

    # -- أنواع الشرائح ------------------------------------------------
    def cover(self, title: str, subtitle: str, facts: List[str],
              image: Optional[Path]):
        slide = self.new_slide(D.NAVY)
        # زخرفة: دائرتان تخرج أجزاؤهما من حدود الشريحة
        self.box(slide, -2.2, 3.6, 6.4, 6.4, fill="23256B", oval=True)
        self.box(slide, 10.6, -2.6, 5.2, 5.2, fill="23256B", oval=True)
        right = SLIDE_W - MARGIN
        text_w = 7.0 if image else SLIDE_W - 2 * MARGIN
        text_x = right - text_w
        self.box(slide, right - 0.9, 1.35, 0.9, 0.09, fill=D.ACCENT)
        if subtitle:
            self.text(slide, text_x, 1.6, text_w, 0.5, _fit(subtitle, 60), 18,
                      "C9CBF0", anchor="m")
        self.title(slide, title, text_x, 2.15, text_w, 2.5, 40, D.WHITE, anchor="t")
        x = right
        for fact in facts:
            width = max(1.4, 0.15 * len(fact) + 0.55)
            x -= width
            self.chip(slide, x, 5.35, width, 0.46, fact, "2E3090", D.WHITE, 13,
                      bold=False)
            x -= 0.16
        if image is not None:
            try:
                self.picture(slide, image, MARGIN, 1.6, 4.6, 3.45,
                             alt="لقطة من التسجيل", frame="3B3DA8", radius=0.18)
            except Exception as exc:
                logger.warning(f"تعذّرت صورة الغلاف {image.name}: {exc}")
        return slide

    def agenda(self, page: List[Tuple[int, str]], part: int, parts: int):
        slide = self.new_slide(D.PAPER)
        label = "محتويات العرض" + (f" ({part}/{parts})" if parts > 1 else "")
        self.box(slide, SLIDE_W - MARGIN - 0.12, 0.62, 0.12, 0.72, fill=D.ACCENT)
        self.title(slide, label, 1.6, 0.55, SLIDE_W - 1.6 - MARGIN - 0.35, 0.86,
                   32, D.NAVY)
        cols = 1 if len(page) <= AGENDA_ROWS else 2 if len(page) <= 2 * AGENDA_ROWS else 3
        size = {1: 20, 2: 16, 3: 14}[cols]
        gap = 0.3
        col_w = (SLIDE_W - 2 * MARGIN - gap * (cols - 1)) / cols
        top, row_h = 1.85, 0.57
        for index, (number, title) in enumerate(page):
            col, row = divmod(index, AGENDA_ROWS)
            x = SLIDE_W - MARGIN - (col + 1) * col_w - col * gap
            y = top + row * row_h
            self.box(slide, x, y, col_w, row_h - 0.1, fill=D.WHITE, line=D.LINE,
                     radius=0.1)
            self.chip(slide, x + col_w - 0.5, y + 0.07, 0.38, 0.38, str(number),
                      D.NAVY, D.WHITE, 11)
            self.text(slide, x + 0.12, y, col_w - 0.75, row_h - 0.1,
                      _fit(title, 48 if cols == 3 else 70), size, D.INK,
                      anchor="m", spacing=1.0)
        self.footer(slide)
        return slide

    def summary(self, abstract: str, points: List[str]):
        slide = self.new_slide(D.PAPER)
        self.box(slide, SLIDE_W - MARGIN - 0.12, 0.62, 0.12, 0.72, fill=D.ACCENT)
        self.title(slide, "الملخّص", 1.6, 0.55, SLIDE_W - 1.6 - MARGIN - 0.35, 0.86,
                   32, D.NAVY)
        y = 1.8
        if abstract:
            text = _fit(abstract, 260)
            lines = _lines_needed(text, SLIDE_W - 2 * MARGIN - 0.8, 18)
            height = min(1.9, lines * 0.36 + 0.4)
            self.box(slide, MARGIN, y, SLIDE_W - 2 * MARGIN, height, fill=D.NAVY,
                     radius=0.14)
            self.text(slide, MARGIN + 0.35, y, SLIDE_W - 2 * MARGIN - 0.7, height,
                      text, 18, D.WHITE, anchor="m", spacing=1.2)
            y += height + 0.3
        if points:
            cols, gap = 2, 0.25
            col_w = (SLIDE_W - 2 * MARGIN - gap) / cols
            avail = SLIDE_H - 0.75 - y
            rows = math.ceil(len(points) / cols)
            card_h = min(1.1, (avail - 0.18 * (rows - 1)) / rows)
            for index, point in enumerate(points):
                row, col = divmod(index, cols)
                x = SLIDE_W - MARGIN - (col + 1) * col_w - col * gap
                cy = y + row * (card_h + 0.18)
                self.box(slide, x, cy, col_w, card_h, fill=D.WHITE, line=D.LINE,
                         radius=0.12)
                self.box(slide, x + col_w - 0.08, cy + 0.14, 0.08, card_h - 0.28,
                         fill=D.ACCENT)
                self.text(slide, x + 0.2, cy, col_w - 0.55, card_h,
                          _fit(point, 110), 16, D.INK, anchor="m", spacing=1.1)
        self.footer(slide)
        self.notes(slide, abstract)
        return slide

    def section(self, number: int, total: int, title: str, summary: str,
                bullets: List[str], notes: str):
        slide = self.new_slide(D.PAPER)
        self.box(slide, SLIDE_W - MARGIN - 0.12, 0.62, 0.12, 0.98, fill=D.ACCENT)
        self.title(slide, title, 2.2, 0.5, SLIDE_W - 2.2 - MARGIN - 0.35, 1.2,
                   30, D.NAVY)
        self.chip(slide, MARGIN, 0.82, 1.35, 0.42, f"{number}/{total}",
                  D.BRAND_LIGHT, D.BRAND, 13)
        y = 1.95
        if summary:
            self.text(slide, MARGIN, y, SLIDE_W - 2 * MARGIN - 0.35, 0.6,
                      _fit(summary, 150), 16, D.MUTED, anchor="t", spacing=1.1)
            y += 0.75
        self._bullet_cards(slide, bullets, y, SLIDE_H - 0.75)
        self.footer(slide)
        self.notes(slide, notes)
        return slide

    def _bullet_cards(self, slide, bullets: List[str], top: float, bottom: float):
        if not bullets:
            return
        full_w = SLIDE_W - 2 * MARGIN
        parsed = [D.split_step(b) for b in bullets]
        numbered = all(num for num, _ in parsed)
        text_w = full_w - (1.35 if numbered else 0.9)
        gap = 0.14
        for size in ((24, 22, 20, 18, 16) if len(bullets) <= 3 else (22, 20, 18, 16)):
            heights = [max(0.66, _lines_needed(t, text_w, size) * size * 1.4 / 72 + 0.32)
                       for _, t in parsed]
            if sum(heights) + gap * (len(heights) - 1) <= bottom - top:
                break
        y = top
        for (num, body), height in zip(parsed, heights):
            if y + height > bottom + 0.01:
                break  # الباقي في ملاحظات المتحدّث لا على الشريحة
            self.box(slide, MARGIN, y, full_w, height, fill=D.WHITE, line=D.LINE,
                     radius=0.12)
            if numbered:
                self.chip(slide, SLIDE_W - MARGIN - 0.78, y + (height - 0.5) / 2,
                          0.5, 0.5, num, D.NAVY, D.WHITE, 16)
            else:
                self.box(slide, SLIDE_W - MARGIN - 0.09, y + 0.14, 0.09,
                         height - 0.28, fill=D.ACCENT)
            self.text(slide, MARGIN + 0.25, y, text_w, height, body, size,
                      D.INK, anchor="m", spacing=1.1)
            y += height + gap

    def figure(self, image_path: Path, section_title: str, caption: str,
               timestamp: Optional[float], index: int, total: int, alt: str):
        slide = self.new_slide(D.STAGE)
        caption = D.clean_caption(caption)
        label_parts = [section_title]
        if total > 1:
            label_parts.append(f"{index}/{total}")
        if caption:
            self.text(slide, MARGIN, 0.36, SLIDE_W - 2 * MARGIN, 0.34,
                      _fit(" · ".join(label_parts), 90), 13, "9A9DC8", anchor="m")
            headline = caption
        else:
            headline = section_title
        self.title(slide, _fit(headline, 120), MARGIN, 0.7 if caption else 0.5,
                   SLIDE_W - 2 * MARGIN, 0.95, 22, D.WHITE)
        top = 1.8
        bottom = SLIDE_H - 0.68
        _, box = self.picture(slide, image_path, MARGIN, top, SLIDE_W - 2 * MARGIN,
                              bottom - top, alt=alt, frame="FFFFFF")
        if timestamp is not None:
            self.chip(slide, MARGIN, SLIDE_H - 0.5, 1.15, 0.34,
                      seconds_to_display(timestamp), "2E3070", "C9CBF0", 11,
                      bold=False)
        self.text(slide, SLIDE_W - MARGIN - 1.2, SLIDE_H - 0.5, 1.2, 0.34,
                  str(self.count), 11, "9A9DC8", align="l", anchor="m")
        return slide

    def closing(self, title: str, facts: List[str]):
        slide = self.new_slide(D.NAVY)
        self.box(slide, 4.6, 2.2, 8.4, 8.4, fill="23256B", oval=True)
        right = SLIDE_W - MARGIN
        self.box(slide, right - 0.9, 2.35, 0.9, 0.09, fill=D.ACCENT)
        self.title(slide, "نهاية العرض", MARGIN + 3.0, 2.6, right - MARGIN - 3.0, 1.2,
                   44, D.WHITE)
        self.text(slide, MARGIN + 3.0, 3.85, right - MARGIN - 3.0, 1.2,
                  _fit(title, 100), 20, "C9CBF0", spacing=1.2)
        if facts:
            self.text(slide, MARGIN + 3.0, 5.2, right - MARGIN - 3.0, 0.4,
                      "  ·  ".join(facts), 14, "9A9DC8")
        return slide


# ---------------------------------------------------------------------
def export(ctx: ExportContext) -> Optional[Path]:
    try:
        from pptx import Presentation
        from pptx.util import Inches
    except ModuleNotFoundError:
        logger.info(INSTALL_HINT)
        return None

    plan = ctx.plan
    if not plan.sections:
        logger.warning("خطة بلا أقسام — تُخطّى الشرائح.")
        return None

    font = getattr(ctx.document_config, "font_family", None) or "Arial"
    title = plan.title or ctx.base_path.stem

    presentation = Presentation()
    presentation.slide_width = Inches(SLIDE_W)   # ‏16:9
    presentation.slide_height = Inches(SLIDE_H)
    deck = _Deck(presentation, font, title)

    # الصور الصالحة لكل قسم — تُحسب مرّة: الغلاف والأعداد يحتاجانها
    def figures_of(section):
        found = []
        for block in section.blocks:
            if block.kind != "figure" or not block.image_filename:
                continue
            path = ctx.images_dir / block.image_filename
            if path.is_file():
                found.append((block, path))
        return found

    per_section = [figures_of(s) for s in plan.sections]
    total_figures = sum(len(f) for f in per_section)
    first_image = next((f[0][1] for f in per_section if f), None)

    facts = [f"المدة {seconds_to_display(ctx.metadata.duration_seconds)}",
             f"{len(plan.sections)} قسمًا"]
    if total_figures:
        facts.append(f"{total_figures} لقطة")
    deck.cover(title, plan.subtitle, facts, first_image)

    section_titles = [s.title or "قسم" for s in plan.sections]
    if len(section_titles) >= 3:
        pages = agenda_pages(section_titles)
        for part, page in enumerate(pages, start=1):
            deck.agenda(page, part, len(pages))

    points = [p for p in (plan.key_points or []) if (p or "").strip()][:MAX_BULLETS]
    if plan.abstract or points:
        deck.summary((plan.abstract or "").strip() if points else "",
                     points or [_fit(plan.abstract, 220)])

    total = len(plan.sections)
    for number, (section, figures) in enumerate(zip(plan.sections, per_section),
                                                 start=1):
        deck.section(
            number, total, section.title or "قسم",
            section.summary or "", section_bullets(section),
            _speaker_notes(section))
        for index, (block, image_path) in enumerate(figures, start=1):
            try:
                deck.figure(image_path, section.title or "قسم", block.caption,
                            block.timestamp, index, len(figures),
                            alt=describe(block, block.image_id))
            except Exception as exc:
                # صورة واحدة تالفة لا تُسقط العرض كلّه
                logger.warning(
                    f"تعذّرت إضافة الشريحة {block.image_filename}: {exc}")

    deck.closing(title, [f"{total} قسمًا"] + ([f"{total_figures} لقطة"]
                                             if total_figures else []))

    props = presentation.core_properties
    props.title = title
    props.subject = plan.subtitle or ""
    props.language = "ar-SA"

    path = ctx.sibling(".pptx")
    path.parent.mkdir(parents=True, exist_ok=True)
    presentation.save(str(path))
    return path


register("pptx", export)
