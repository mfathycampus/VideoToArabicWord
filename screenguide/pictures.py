"""صور الخطوات: العنصر محاطٌ ومرقَّم، والشاشة حوله مقصوصةٌ لتُقرأ.

منقولة من WebDoc (``documentation/illustrate.py`` و``infographic.step_picture``)
بقاعدتين:

1. **الخطوة تُظهر الشاشة التي ينظر إليها القارئ حين ينفّذها** — الصورة
   لحظة ما قبل الخطوة، لا الشاشة التي تؤدّي إليها.
2. **لا يُرسم ما لم يُسجَّل.** العلامة حيث قال المصدر إن العنصر كان، والرقم
   بجانبه لا فوق اسمه، وخطوةٌ بلا صندوق تأخذ الشاشة كما هي.

الترتيب مهم: الصناديق بإحداثيات الصورة الأصلية، فلا يُقصّ شيء من أعلاها أو
يسارها قبل استعمالها. القصّ آخرًا، على صورة لن يقيسها أحد ثانيةً.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple

#: عرضٌ يُقرأ فيه اسم حقل على صفحة A4، وصغيرٌ بما يكفي ليُرسل الدليل بالبريد.
WIDTH = 1400
#: مساحة الشاشة حول العنصر المعلَّم حين تُقصّ الصورة إليه.
MARGIN = 180

#: لون معين (‏ui/theme.ACCENT) لإطار العنصر.
ACCENT = (15, 118, 110)
#: قرص رقم الخطوة: الأحمر نفسه في WebDoc، فيُرى على أي خلفية.
BADGE = (220, 38, 38)
BADGE_RADIUS = 24

#: سياق الإنفوجرافيك: يُقصّ حول العنصر بهذا الهامش وبهذه النسبة.
CONTEXT = 300
MIN_CROP = 760
ASPECT = 0.62
PICTURE_WIDTH = 1120
SUPERSAMPLE = 3

_FONT_CANDIDATES = (
    "arialbd.ttf",
    "segoeuib.ttf",
    "DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)


def badge_font(size: int) -> Any:
    from PIL import ImageFont

    for candidate in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _overlaps(a: Sequence[float], b: Sequence[float]) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def badge_position(canvas_size: Tuple[int, int], box: Sequence[float],
                   radius: int = BADGE_RADIUS) -> Tuple[float, float]:
    """أين يوضع الرقم: **بجانب** العنصر، قبله ثم بعده ثم فوقه ثم تحته."""
    width, height = canvas_size
    x, y, w, h = (float(v) for v in box)
    r = radius + 4
    gap = 6
    candidates = [
        (x - r - gap, y + h / 2),                 # قبله (يساره)
        (x + w + r + gap, y + h / 2),             # بعده
        (x + min(w / 2, r + 4), y - r - gap),     # فوقه
        (x + min(w / 2, r + 4), y + h + r + gap),  # تحته
    ]
    target = (x, y, x + w, y + h)
    for cx, cy in candidates:
        disc = (cx - r, cy - r, cx + r, cy + r)
        if disc[0] < 0 or disc[1] < 0 or disc[2] > width or disc[3] > height:
            continue
        if _overlaps(disc, target):
            continue
        return cx, cy
    cx = min(max(x - radius * 0.35, radius + 2), width - radius - 2)
    cy = min(max(y - radius * 0.35, radius + 2), height - radius - 2)
    return cx, cy


def draw_badge(canvas: Any, box: Sequence[float], number: int) -> None:
    from PIL import ImageDraw

    r = BADGE_RADIUS
    cx, cy = badge_position(canvas.size, box, r)
    drawing = ImageDraw.Draw(canvas, "RGBA")
    drawing.ellipse([cx - r - 4, cy - r - 4, cx + r + 4, cy + r + 4], fill=(0, 0, 0, 60))
    drawing.ellipse([cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3], fill=(255, 255, 255))
    drawing.ellipse([cx - r, cy - r, cx + r, cy + r], fill=BADGE)
    text = str(number)
    font = badge_font(int(r * (1.15 if len(text) == 1 else 0.95)))
    left, top, right, bottom = drawing.textbbox((0, 0), text, font=font)
    drawing.text((cx - (right - left) / 2 - left, cy - (bottom - top) / 2 - top), text,
                 font=font, fill=(255, 255, 255))


def annotate(shot: Path, box: Optional[Sequence[float]], number: Optional[int],
             out: Path, *, width: int = WIDTH) -> Path:
    """صورة الخطوة في الدليل: العنصر مظلَّل ومحاط ومرقَّم، ثم القصّ والتصغير."""
    from PIL import Image, ImageDraw

    with Image.open(shot) as raw:
        canvas = raw.convert("RGB")
    if box and len(box) == 4 and box[2] > 0 and box[3] > 0:
        x, y, w, h = (float(v) for v in box)
        drawing = ImageDraw.Draw(canvas, "RGBA")
        # غشاءٌ شفّاف فوق العنصر مع إطارٍ صلب: يُقرأ على ترويسةٍ داكنة وعلى
        # نموذجٍ أبيض، وهو ما لا يفعله إطارٌ وحده.
        drawing.rectangle([x - 3, y - 3, x + w + 3, y + h + 3], fill=(*ACCENT, 38))
        for inset in range(3):
            drawing.rectangle([x - 3 - inset, y - 3 - inset,
                               x + w + 3 + inset, y + h + 3 + inset], outline=ACCENT)
        if number:
            draw_badge(canvas, (x, y, w, h), number)
        canvas = _crop_around(canvas, (x, y, w, h))
    canvas = _scale(canvas, width)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, format="PNG", optimize=True)
    return out


def _scale(canvas: Any, width: int) -> Any:
    from PIL import Image

    if canvas.width > width:
        height = max(1, round(canvas.height * width / canvas.width))
        canvas = canvas.resize((width, height), Image.LANCZOS)
    return canvas


def _crop_around(canvas: Any, box: Tuple[float, float, float, float]) -> Any:
    """العنصر في سياقه لا وحده — ولا أقلّ من نصف عرض الشاشة."""
    x, y, w, h = box
    left = max(0, x - MARGIN)
    top = max(0, y - MARGIN)
    right = min(canvas.width, x + w + MARGIN)
    bottom = min(canvas.height, y + h + MARGIN)
    if right - left < canvas.width / 2:
        centre = (left + right) / 2
        half = canvas.width / 4
        left, right = max(0, centre - half), min(canvas.width, centre + half)
    if bottom - top < 300:
        centre = (top + bottom) / 2
        top, bottom = max(0, centre - 150), min(canvas.height, centre + 150)
    return canvas.crop((int(left), int(top), int(right), int(bottom)))


# ------------------------------------------------------------ الإنفوجرافيك
def step_picture(shot: Path, box: Optional[Sequence[float]], number: Optional[int],
                 *, width: int = PICTURE_WIDTH) -> bytes:
    """الجزء الذي تعنيه الخطوة من الشاشة، والعنصر مضاءٌ ومرقَّم.

    بقية الشاشة تُعتَّم قليلًا لا تُقصّ: القارئ يرى أين هو، وعينه تذهب إلى
    العنصر أولًا.
    """
    from PIL import Image, ImageDraw

    with Image.open(shot) as raw:
        image = raw.convert("RGB")
    full_w, full_h = image.size
    target = _clip(box, full_w, full_h)
    if target is not None:
        x, y, w, h = target
        crop_w = min(full_w, max(w + 2 * CONTEXT, MIN_CROP))
        crop_h = min(full_h, max(h + CONTEXT, crop_w * ASPECT))
        crop_h = min(crop_h, max(crop_w * 0.9, h + 80))
        left = _clamp(x + w / 2 - crop_w / 2, 0, full_w - crop_w)
        top = _clamp(y + h / 2 - crop_h / 2, 0, full_h - crop_h)
    else:
        crop_w, crop_h = full_w, min(full_h, full_w * ASPECT)
        left = top = 0.0
    region = (int(left), int(top), int(left + crop_w), int(top + crop_h))
    canvas = image.crop(region).convert("RGBA")

    if target is not None:
        x, y, w, h = target
        rel = (x - region[0], y - region[1], w, h)
        s = SUPERSAMPLE
        layer = Image.new("RGBA", (canvas.width * s, canvas.height * s), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        pad = 8
        rx, ry, rw, rh = (v * s for v in rel)
        ring = [rx - pad * s, ry - pad * s, rx + rw + pad * s, ry + rh + pad * s]
        radius = 12 * s
        draw.rectangle([0, 0, layer.width, layer.height], fill=(11, 46, 42, 38))
        draw.rounded_rectangle(ring, radius=radius, fill=(0, 0, 0, 0))
        for grow, alpha in ((14, 40), (9, 70)):
            g = grow * s
            draw.rounded_rectangle([ring[0] - g, ring[1] - g, ring[2] + g, ring[3] + g],
                                   radius=radius + g, outline=(*ACCENT, alpha), width=5 * s)
        draw.rounded_rectangle(ring, radius=radius, outline=(*ACCENT, 255), width=4 * s)
        if number is not None:
            _ring_badge(draw, layer.size, ring, number, s)
        layer = layer.resize(canvas.size, Image.Resampling.LANCZOS)
        canvas = Image.alpha_composite(canvas, layer)

    picture = canvas.convert("RGB")
    if picture.width > width:
        height = max(1, round(picture.height * width / picture.width))
        picture = picture.resize((width, height), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    picture.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _ring_badge(draw: Any, size: Tuple[int, int], ring: list, number: int, s: int) -> None:
    r = 22 * s
    gap = 8 * s
    width, height = size
    x0, y0, x1, y1 = ring
    end = x1 - r
    candidates = ((end, y0 - r - gap), (end, y1 + r + gap),
                  (x1 + r + gap, (y0 + y1) / 2), (x0 - r - gap, (y0 + y1) / 2))
    margin = r + 4 * s
    cx, cy = next(((x, y) for x, y in candidates
                   if margin <= x <= width - margin and margin <= y <= height - margin),
                  (_clamp(x1, margin, width - margin), _clamp(y0, margin, height - margin)))
    draw.ellipse([cx - r - 3 * s, cy - r - 3 * s, cx + r + 3 * s, cy + r + 3 * s],
                 fill=(255, 255, 255, 255))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*BADGE, 255))
    font = badge_font(25 * s)
    text = str(number)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (left + right) / 2, cy - (top + bottom) / 2), text, font=font,
              fill=(255, 255, 255, 255))


def _clip(box: Optional[Sequence[float]], width: int,
          height: int) -> Optional[Tuple[float, float, float, float]]:
    if not box or len(box) < 4:
        return None
    x, y, w, h = (float(v) for v in box[:4])
    if w <= 0 or h <= 0:
        return None
    left, top = max(0.0, x), max(0.0, y)
    right, bottom = min(float(width), x + w), min(float(height), y + h)
    if right <= left or bottom <= top:
        return None
    return (left, top, right - left, bottom - top)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high)) if high >= low else low
