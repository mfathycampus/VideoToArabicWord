"""رحلةٌ واحدة، صورةٌ واحدة: الإجراء إنفوجرافيكًا.

الشكل الذي تتناقله المدارس على واتساب — صورة طويلة واحدة، شريط عنوان،
خطواتٌ مرقّمة كلٌّ منها بالجزء الذي يهمّ من الشاشة، مجمّعةٌ بالنظام الذي
تقع فيه («أولاً: في «نور»») — مصنوعًا مما سُجّل فعلًا لا من لقطاتٍ ملصوقة
باليد. منقولٌ من WebDoc (``documentation/infographic.py``).

شيئان يُبقيانه حادًّا:

* صورة كل خطوة تُقصّ من اللقطة **الأصلية** بدقّتها حول العنصر، ثم تُصغَّر —
  لا مصغّرة من مصغّرة (``pictures.step_picture``).
* الصفحة تُرسم بمحرّك نصوص حقيقي يشكّل العربية ويرتّبها (Qt)، بضعف الكثافة
  — لا حرفًا حرفًا على صورة.

WebDoc يصوّر صفحة HTML بمتصفّحه المدمج؛ معين لا يشحن متصفّحًا، فالتخطيط
نفسه يُرسم بـ ``QPainter``، وتُكتب نسخة HTML بجانب الصورة للطباعة والمشاركة.
"""
from __future__ import annotations

import base64
import html
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Optional, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from screenguide.guide import Guide

ORDINALS_AR = ("أولاً", "ثانياً", "ثالثاً", "رابعاً", "خامساً", "سادساً", "سابعاً",
               "ثامناً", "تاسعاً", "عاشراً")

#: ألوان معين (ui/theme): حبر الشعار والفيروزي.
INK = "#0B2E2A"
DEEP = "#115E59"
ACCENT = "#0F766E"
LIGHT = "#5EEAD4"
MUTED = "#475569"
LINE = "#E2E8F0"
BG = "#EEF5F3"
BADGE = "#DC2626"
OK = "#047857"

#: عرض الصفحة بالبكسل المنطقي، ومضاعف الكثافة عند الرسم.
PAGE_WIDTH = 1600
SCALE = 2


@dataclass
class PosterStep:
    number: int
    instruction: str
    result: Optional[str] = None
    #: بايتات PNG لصورة الخطوة، مقصوصة ومعلَّمة (``pictures.step_picture``).
    picture: Optional[bytes] = None
    #: النظام أو البرنامج الذي تقع فيه الخطوة.
    group: Optional[str] = None


@dataclass
class Poster:
    title: str
    steps: List[PosterStep]
    language: str = "ar"
    #: الدليل الذي جاء منه، يظهر شريحةً تحت العنوان.
    source: str = ""
    #: النظام الوحيد الذي تقع فيه الرحلة كلّها.
    system: str = ""
    start: List[str] = field(default_factory=list)
    results: List[str] = field(default_factory=list)
    footer: str = ""
    version: str = ""
    made_on: Optional[date] = None


def columns_for(count: int) -> int:
    if count <= 4:
        return 2
    if count <= 24:
        return 3
    return 4


def groups_of(steps: Sequence[PosterStep]) -> List[Tuple[Optional[str], List[PosterStep]]]:
    """الخطوات المتتالية في النظام نفسه معًا، بترتيب وقوعها.

    خطوةٌ لا يُعرف نظامها تبقى مع التي قبلها.
    """
    out: List[Tuple[Optional[str], List[PosterStep]]] = []
    for step in steps:
        key = step.group or None
        if out and (key is None or key == out[-1][0]):
            out[-1][1].append(step)
        elif out and out[-1][0] is None:
            out[-1] = (key, [*out[-1][1], step])
        else:
            out.append((key, [step]))
    return out


def count_label(n: int, ar: bool) -> str:
    if not ar:
        return f"{n} step" if n == 1 else f"{n} steps"
    if n == 1:
        return "خطوة واحدة"
    if n == 2:
        return "خطوتان"
    return f"{n} خطوات" if 3 <= n <= 10 else f"{n} خطوة"


def _q(text: str, ar: bool) -> str:
    return f"«{text}»" if ar else f"“{text}”"


def _e(text: str) -> str:
    return html.escape(text or "", quote=True)


_QUOTED = re.compile(r"(«[^»]{1,80}»|“[^”]{1,80}”)")


def marked(text: Optional[str], color: str = DEEP) -> str:
    """مُهرَّب، وأسماء العناصر بين «» بالخط العريض — ما تبحث عنه العين."""
    if not text:
        return ""
    # الاسم بين «» لا يُكسر على سطرين: «File» آخر سطر و«name» أول التالي
    # يقرؤه القارئ اسمين. المسافات داخله غير قابلة للكسر.
    return "".join(
        f'<b style="color:{color}">{_e(part).replace(" ", "&nbsp;")}</b>'
        if _QUOTED.fullmatch(part) else _e(part)
        for part in _QUOTED.split(text))


def _brand(ar: bool) -> str:
    return "أُعدّ بواسطة معين من تسجيل فعلي" if ar else "Made with Moeen from a real recording"


def _chips(poster: Poster, ar: bool, show_groups: bool) -> List[str]:
    chips = [poster.source] if poster.source else []
    if poster.system and not show_groups:
        chips.append(("في " if ar else "In ") + _q(poster.system, ar))
    chips.append(count_label(len(poster.steps), ar))
    return chips


def _group_heading(index: int, group: str, ar: bool) -> str:
    ordinal = ORDINALS_AR[index] if ar and index < len(ORDINALS_AR) else f"{index + 1}"
    return f"{ordinal}: في {_q(group, ar)}" if ar else f"{ordinal}. In {_q(group, ar)}"


def _footer(poster: Poster) -> str:
    made = (poster.made_on or date.today()).isoformat()
    bits = [b for b in (poster.footer, f"v{poster.version}" if poster.version else "", made) if b]
    return " · ".join(bits)


# ======================================================================== HTML
def poster_html(poster: Poster) -> str:
    """الإنفوجرافيك صفحةً واحدة — للطباعة أو الفتح في أي متصفّح."""
    ar = poster.language.startswith("ar")
    groups = groups_of(poster.steps)
    show_groups = len([g for g, _ in groups if g]) > 1
    cols = columns_for(len(poster.steps))
    chips = "".join(f'<span class="chip">{_e(c)}</span>' for c in _chips(poster, ar, show_groups))
    start = "".join(f"<li>{marked(item, '#fff')}</li>" for item in poster.start)

    sections: List[str] = []
    for index, (group, steps) in enumerate(groups):
        heading = ""
        if show_groups and group:
            arrow = "◀" if ar else "▶"
            heading = (f'<h2 class="group"><span class="arrow">{arrow}</span>'
                       f'{_e(_group_heading(index, group, ar))}</h2>')
        cards = "".join(_card(step) for step in steps)
        sections.append(f'<section>{heading}<div class="grid">{cards}</div></section>')

    results = ""
    if poster.results:
        label = "النتيجة" if ar else "Result"
        items = "".join(f"<li>{marked(r)}</li>" for r in poster.results)
        results = (f'<div class="outcome"><span class="tick">✓</span>'
                   f'<div><strong>{label}</strong><ul>{items}</ul></div></div>')

    return f"""<!doctype html>
<html lang="{'ar' if ar else 'en'}" dir="{'rtl' if ar else 'ltr'}">
<head><meta charset="utf-8"><title>{_e(poster.title)}</title>
<style>{_CSS.replace('__COLS__', str(cols))}</style></head>
<body><div class="poster">
<header>
  <div class="kicker">{'دليل خطوة بخطوة' if ar else 'Step-by-step guide'}</div>
  <h1>{_e(poster.title)}</h1>
  <div class="chips">{chips}</div>
  {f'<ul class="start">{start}</ul>' if start else ''}
</header>
<main>{''.join(sections)}{results}</main>
<footer><span>{_e(_footer(poster))}</span><span class="brand">{_e(_brand(ar))}</span></footer>
</div></body></html>"""


def _card(step: PosterStep) -> str:
    picture = ""
    if step.picture:
        data = base64.b64encode(step.picture).decode("ascii")
        picture = f'<div class="shot"><img src="data:image/png;base64,{data}" alt=""></div>'
    result = f'<div class="result">{marked(step.result)}</div>' if step.result else ""
    return (f'<article class="card">'
            f'<div class="head"><span class="num">{step.number}</span>'
            f'<div><div class="text">{marked(step.instruction)}</div>{result}</div></div>'
            f'{picture}</article>')


_CSS = f"""
* {{ box-sizing:border-box; margin:0; padding:0; }}
html, body {{ background:{BG}; }}
body {{ max-width:1600px; margin:0 auto; color:#0f172a;
       font-family:"Segoe UI","Tahoma","Noto Sans Arabic","Arial",sans-serif; }}
.poster {{ padding-bottom:36px; }}
header {{ position:relative; overflow:hidden; color:#fff; padding:54px 72px 58px;
         background:linear-gradient(135deg,{INK} 0%,{DEEP} 45%,{ACCENT} 75%,#14B8A6 100%); }}
.kicker {{ font-size:20px; font-weight:600; opacity:.85; }}
h1 {{ font-size:54px; font-weight:800; line-height:1.3; margin-top:8px; }}
.chips {{ display:flex; flex-wrap:wrap; gap:12px; margin-top:20px; }}
.chip {{ font-size:20px; padding:7px 18px; border-radius:999px;
        background:rgba(255,255,255,.15); border:1px solid rgba(255,255,255,.35); }}
.start {{ list-style:none; margin-top:18px; font-size:21px; opacity:.95; }}
.start li::before {{ content:"●"; margin-inline-end:10px; color:{LIGHT}; }}
main {{ padding:12px 56px 0; }}
section {{ margin-top:34px; }}
h2.group {{ display:inline-flex; align-items:center; gap:14px; font-size:30px; font-weight:800;
           color:{DEEP}; background:#fff; padding:12px 30px; border-radius:16px;
           border:2px solid #99F6E4; margin-bottom:22px; }}
h2.group .arrow {{ font-size:18px; color:{ACCENT}; }}
.grid {{ display:flex; flex-wrap:wrap; justify-content:center; align-items:flex-start; gap:28px; }}
.grid > .card {{ width:calc((100% - (__COLS__ - 1) * 28px) / __COLS__); }}
.card {{ background:#fff; border-radius:22px; border:1px solid {LINE}; overflow:hidden;
        box-shadow:0 1px 2px rgba(15,23,42,.05), 0 10px 28px rgba(15,23,42,.08); }}
.head {{ display:flex; gap:16px; align-items:flex-start; padding:22px 24px 18px; }}
.num {{ flex:none; width:54px; height:54px; border-radius:50%; display:grid; place-items:center;
       color:#fff; font-size:26px; font-weight:800; background:{BADGE};
       box-shadow:0 0 0 4px #fee2e2; }}
.text {{ font-size:22px; font-weight:600; line-height:1.6; padding-top:8px; }}
.result {{ margin-top:6px; font-size:18px; color:{MUTED}; line-height:1.5; }}
.shot {{ margin:0 20px 20px; border-radius:16px; overflow:hidden; border:1px solid {LINE}; }}
.shot img {{ display:block; width:100%; height:auto; }}
.outcome {{ display:flex; gap:18px; align-items:flex-start; margin-top:34px; padding:24px 30px;
           border-radius:20px; background:#ecfdf5; border:2px solid #a7f3d0; color:#064e3b; }}
.outcome .tick {{ flex:none; width:46px; height:46px; border-radius:50%; display:grid;
                 place-items:center; background:{OK}; color:#fff; font-size:26px; font-weight:800; }}
.outcome strong {{ font-size:24px; }}
.outcome ul {{ list-style:none; margin-top:6px; font-size:21px; line-height:1.6; }}
footer {{ display:flex; justify-content:space-between; gap:20px; margin:38px 56px 0;
         padding-top:18px; border-top:1px solid #cbd5e1; color:{MUTED}; font-size:17px; }}
footer .brand {{ font-weight:600; }}
@media print {{ body {{ max-width:none; }} }}
"""


# ======================================================================== PNG
def render_png(poster: Poster, path: Path) -> Path:
    """يرسم الإنفوجرافيك صورة PNG بـ Qt (يحتاج ``QGuiApplication`` قائمًا)."""
    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import (
        QBrush,
        QColor,
        QFont,
        QImage,
        QLinearGradient,
        QPainter,
        QPainterPath,
        QPen,
        QTextDocument,
        QTextOption,
    )

    ar = poster.language.startswith("ar")
    direction = Qt.LayoutDirection.RightToLeft if ar else Qt.LayoutDirection.LeftToRight
    families = ["Segoe UI", "Tahoma", "Noto Sans Arabic", "DejaVu Sans", "Arial"]

    def font(px: float, weight: int = 400) -> QFont:
        f = QFont()
        f.setFamilies(families)
        f.setPixelSize(int(px))
        f.setWeight(QFont.Weight(weight))
        return f

    def doc(html_text: str, width: float, px: float, color: str, weight: int = 400,
            line: float = 1.55) -> QTextDocument:
        d = QTextDocument()
        d.setDocumentMargin(0)
        d.setDefaultFont(font(px, weight))
        option = QTextOption()
        option.setTextDirection(direction)
        option.setAlignment(Qt.AlignmentFlag.AlignRight if ar else Qt.AlignmentFlag.AlignLeft)
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        d.setDefaultTextOption(option)
        align = "right" if ar else "left"
        dir_ = "rtl" if ar else "ltr"
        d.setHtml(f'<div dir="{dir_}" align="{align}" style="color:{color};'
                  f'line-height:{int(line * 100)}%">{html_text}</div>')
        d.setTextWidth(width)
        return d

    def draw_doc(p: QPainter, d: QTextDocument, x: float, y: float) -> None:
        p.save()
        p.translate(x, y)
        d.drawContents(p)
        p.restore()

    groups = groups_of(poster.steps)
    show_groups = len([g for g, _ in groups if g]) > 1
    cols = columns_for(len(poster.steps))
    W = PAGE_WIDTH
    pad_x = 56
    gap = 28
    card_w = (W - 2 * pad_x - (cols - 1) * gap) / cols

    # ---------------------------------------------------------- القياس أولًا
    header_pad = 72
    inner = W - 2 * header_pad
    kicker = doc(_e("دليل خطوة بخطوة" if ar else "Step-by-step guide"), inner, 20,
                 "#CCFBF1", 600)
    title = doc(_e(poster.title), inner, 54, "#ffffff", 800, 1.3)
    chips = _chips(poster, ar, show_groups)
    start_docs = [doc("● " + marked(s, "#ffffff"), inner, 21, "#E6FFFA") for s in poster.start]
    chip_font = font(20)
    from PyQt6.QtGui import QFontMetricsF

    chip_metrics = QFontMetricsF(chip_font)
    chip_h = 40.0
    header_h = 54 + kicker.size().height() + 8 + title.size().height() + 20 + chip_h
    if start_docs:
        header_h += 18 + sum(d.size().height() + 4 for d in start_docs)
    header_h += 58

    cards = []  # (step, head_doc, result_doc, image, height)
    for step in poster.steps:
        text_w = card_w - 24 * 2 - 54 - 16
        head = doc(marked(step.instruction), text_w, 22, "#0f172a", 600, 1.6)
        res = doc(marked(step.result), text_w, 18, MUTED) if step.result else None
        image = QImage()
        if step.picture:
            image.loadFromData(step.picture)
        text_h = 8 + head.size().height() + ((6 + res.size().height()) if res else 0)
        h = 22 + max(54.0, text_h) + 18
        if not image.isNull():
            shot_w = card_w - 40
            h += shot_w * image.height() / max(1, image.width()) + 20
        cards.append((step, head, res, image, h))
    by_number = {c[0].number: c for c in cards}

    layout: List[Tuple[str, object, float]] = []  # (kind, payload, height)
    y = header_h + 12
    for index, (group, steps) in enumerate(groups):
        y += 34
        if show_groups and group:
            heading = _group_heading(index, group, ar)
            layout.append(("group", (heading, y), 0))
            y += 30 * 1.6 + 24 + 22
        rows = [steps[i:i + cols] for i in range(0, len(steps), cols)]
        for row in rows:
            row_h = max(by_number[s.number][4] for s in row)
            layout.append(("row", (row, y), row_h))
            y += row_h + gap
        y -= gap
    result_docs = [doc("• " + marked(r), W - 2 * pad_x - 30 * 2 - 46 - 18, 21, "#064e3b")
                   for r in poster.results]
    if result_docs:
        y += 34
        outcome_h = 24 + 34 + 6 + sum(d.size().height() for d in result_docs) + 24
        layout.append(("outcome", y, outcome_h))
        y += outcome_h
    footer_y = y + 38
    height = int(footer_y + 18 + 30 + 36)

    # --------------------------------------------------------------- الرسم
    image = QImage(int(W * SCALE), int(height * SCALE), QImage.Format.Format_ARGB32)
    image.fill(QColor(BG))
    p = QPainter(image)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                     | QPainter.RenderHint.SmoothPixmapTransform)
    p.scale(SCALE, SCALE)
    p.setLayoutDirection(direction)

    gradient = QLinearGradient(QPointF(0, 0), QPointF(W, header_h))
    for stop, color in ((0.0, INK), (0.45, DEEP), (0.75, ACCENT), (1.0, "#14B8A6")):
        gradient.setColorAt(stop, QColor(color))
    p.fillRect(QRectF(0, 0, W, header_h), QBrush(gradient))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 18))
    p.drawEllipse(QPointF(W - 100 if ar else 100, -10), 260, 260)
    p.drawEllipse(QPointF(80 if ar else W - 80, header_h + 30), 170, 170)

    hy = 54.0
    draw_doc(p, kicker, header_pad, hy)
    hy += kicker.size().height() + 8
    draw_doc(p, title, header_pad, hy)
    hy += title.size().height() + 20
    cx = W - header_pad if ar else header_pad
    p.setFont(chip_font)
    for chip in chips:
        tw = chip_metrics.horizontalAdvance(chip) + 36
        rect = QRectF(cx - tw if ar else cx, hy, tw, chip_h)
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        p.setBrush(QColor(255, 255, 255, 38))
        p.drawRoundedRect(rect, chip_h / 2, chip_h / 2)
        p.setPen(QColor("#ffffff"))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, chip)
        cx = cx - tw - 12 if ar else cx + tw + 12
    hy += chip_h
    if start_docs:
        hy += 18
        for d in start_docs:
            draw_doc(p, d, header_pad, hy)
            hy += d.size().height() + 4

    def x_for(col: int, count: int) -> float:
        row_w = count * card_w + (count - 1) * gap
        left = pad_x + (W - 2 * pad_x - row_w) / 2
        index = (count - 1 - col) if ar else col
        return left + index * (card_w + gap)

    for kind, payload, h in layout:
        if kind == "group":
            text, gy = payload
            gfont = font(30, 800)
            metrics = QFontMetricsF(gfont)
            arrow = "◀ " if ar else "▶ "
            tw = metrics.horizontalAdvance(arrow + text) + 60
            gh = 30 * 1.6 + 24
            rect = QRectF(W - pad_x - tw if ar else pad_x, gy, tw, gh)
            p.setPen(QPen(QColor("#99F6E4"), 2))
            p.setBrush(QColor("#ffffff"))
            p.drawRoundedRect(rect, 16, 16)
            p.setFont(gfont)
            p.setPen(QColor(DEEP))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, arrow + text)
        elif kind == "row":
            row, ry = payload
            for col, step in enumerate(row):
                _, head, res, picture, card_h = by_number[step.number]
                x = x_for(col, len(row))
                rect = QRectF(x, ry, card_w, card_h)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(15, 23, 42, 14))
                p.drawRoundedRect(rect.translated(0, 6), 22, 22)
                p.setPen(QPen(QColor(LINE), 1))
                p.setBrush(QColor("#ffffff"))
                p.drawRoundedRect(rect, 22, 22)
                # الرقم
                disc_x = x + card_w - 24 - 54 if ar else x + 24
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#FEE2E2"))
                p.drawEllipse(QRectF(disc_x - 4, ry + 22 - 4, 62, 62))
                p.setBrush(QColor(BADGE))
                p.drawEllipse(QRectF(disc_x, ry + 22, 54, 54))
                p.setFont(font(26, 800))
                p.setPen(QColor("#ffffff"))
                p.drawText(QRectF(disc_x, ry + 22, 54, 54), Qt.AlignmentFlag.AlignCenter,
                           str(step.number))
                text_x = x + 24 if ar else x + 24 + 54 + 16
                ty = ry + 22 + 8
                draw_doc(p, head, text_x, ty)
                ty += head.size().height()
                if res is not None:
                    draw_doc(p, res, text_x, ty + 6)
                    ty += 6 + res.size().height()
                if not picture.isNull():
                    top = ry + 22 + max(54.0, ty - ry - 22) + 18
                    shot_w = card_w - 40
                    shot_h = shot_w * picture.height() / max(1, picture.width())
                    target = QRectF(x + 20, top, shot_w, shot_h)
                    clip = QPainterPath()
                    clip.addRoundedRect(target, 16, 16)
                    p.save()
                    p.setClipPath(clip)
                    p.drawImage(target, picture)
                    p.restore()
                    p.setPen(QPen(QColor(LINE), 1))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRoundedRect(target, 16, 16)
        elif kind == "outcome":
            oy = payload
            rect = QRectF(pad_x, oy, W - 2 * pad_x, h)
            p.setPen(QPen(QColor("#a7f3d0"), 2))
            p.setBrush(QColor("#ecfdf5"))
            p.drawRoundedRect(rect, 20, 20)
            tick_x = rect.right() - 30 - 46 if ar else rect.left() + 30
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(OK))
            p.drawEllipse(QRectF(tick_x, oy + 24, 46, 46))
            p.setFont(font(26, 800))
            p.setPen(QColor("#ffffff"))
            p.drawText(QRectF(tick_x, oy + 24, 46, 46), Qt.AlignmentFlag.AlignCenter, "✓")
            text_x = rect.left() + 30 if ar else tick_x + 46 + 18
            text_w = rect.width() - 30 * 2 - 46 - 18
            p.setFont(font(24, 700))
            p.setPen(QColor("#064e3b"))
            align = (Qt.AlignmentFlag.AlignRight if ar else Qt.AlignmentFlag.AlignLeft)
            p.drawText(QRectF(text_x, oy + 24, text_w, 34),
                       align | Qt.AlignmentFlag.AlignVCenter, "النتيجة" if ar else "Result")
            ty = oy + 24 + 34 + 6
            for d in result_docs:
                draw_doc(p, d, text_x, ty)
                ty += d.size().height()

    p.setPen(QPen(QColor("#cbd5e1"), 1))
    p.drawLine(QPointF(pad_x, footer_y), QPointF(W - pad_x, footer_y))
    p.setFont(font(17))
    p.setPen(QColor(MUTED))
    row = QRectF(pad_x, footer_y + 18, W - 2 * pad_x, 30)
    start_align = Qt.AlignmentFlag.AlignRight if ar else Qt.AlignmentFlag.AlignLeft
    end_align = Qt.AlignmentFlag.AlignLeft if ar else Qt.AlignmentFlag.AlignRight
    p.drawText(row, start_align | Qt.AlignmentFlag.AlignVCenter, _footer(poster))
    p.setFont(font(17, 600))
    p.drawText(row, end_align | Qt.AlignmentFlag.AlignVCenter, _brand(ar))
    p.end()

    path.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(path), "PNG"):
        raise OSError(f"تعذّر حفظ الإنفوجرافيك: {path}")
    return path


def _qt_ready() -> bool:
    try:
        from PyQt6.QtGui import QGuiApplication
    except Exception:
        return False
    return QGuiApplication.instance() is not None


def write_infographics(guide: "Guide", *, log: Optional[Callable[[str], None]] = None
                       ) -> List[Path]:
    """صورةٌ واحدة (ونسخة HTML) لكل رحلة في الدليل، في ``infographics/``."""
    from screenguide.guide import poster_for, safe_name

    say = log or (lambda _m: None)
    folder = guide.folder / "infographics"
    folder.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    used: set = set()
    can_draw = _qt_ready()
    for journey in guide.journeys():
        poster = poster_for(guide, journey)
        if not poster.steps:
            continue
        name = safe_name(poster.title, "إجراء")
        stem, n = name, 2
        while stem in used:
            stem, n = f"{name} ({n})", n + 1
        used.add(stem)
        html_path = folder / f"{stem}.html"
        html_path.write_text(poster_html(poster), encoding="utf-8")
        written.append(html_path)
        if can_draw:
            try:
                written.append(render_png(poster, folder / f"{stem}.png"))
            except Exception as exc:  # مخرج إضافي لا شرط نجاح
                say(f"تعذّر رسم إنفوجرافيك «{poster.title}»: {exc}")
    if written:
        say(f"الإنفوجرافيك: {len([p for p in written if p.suffix == '.png'])} صورة "
            f"في مجلد infographics")
    return written
