"""يرسم شعار «معين» الأفقي: العلامة + الاسم بالعربية + Maeen.

    python tools/make_wordmark.py

يكتب في ``assets/``:

* ``logo_wordmark.png`` — على أرضيّة فاتحة (المستند، README، الموقع).
* ``logo_wordmark_light.png`` — على الأرضيّة الداكنة (شريط الواجهة).
* ``logo_wordmark.svg`` — مصدر متّجه، الحروف فيه **مسارات** لا نصّ،
  فيظهر كما هو على أي جهاز بلا خط Cairo.

**لماذا نشحن الخط في ``tools/fonts``:** الخط لا يحتاجه البرنامج وقت
التشغيل (الناتج صور ومسارات جاهزة)، لكن إعادة التوليد تحتاجه. Cairo
بترخيص SIL OFL 1.1، ونصّ الترخيص بجواره.

**لماذا ``uharfbuzz`` للـSVG:** تشكيل الحروف العربية (اتّصالها وصيغها)
يحتاج محرّك تشكيل؛ كتابة ``<text>`` في SVG تترك التشكيل للجهاز الذي
يفتح الملف. نُشكّل هنا مرّة ونخزّن المحيط الناتج.

الألوان والعلامة نفسها من ``tools/make_logo.py`` — لا نسخة ثانية.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FONTS = ROOT / "tools" / "fonts"
ARABIC_FONT = FONTS / "Cairo-Arabic.ttf"
LATIN_FONT = FONTS / "Cairo-Latin.ttf"

NAME_AR = "معين"
NAME_LATIN = "Maeen"

#: ارتفاع اللوحة، وكل القياسات الأخرى نِسَبٌ منه.
HEIGHT = 400
#: الاسم العربي: ارتفاع الخط بالنسبة لارتفاع اللوحة.
AR_SIZE = 0.56
LATIN_SIZE = 0.15
#: تباعد أحرف Maeen — النصّ اللاتيني الصغير يتنفّس بتباعد، والعربي لا.
LATIN_TRACKING = 0.22
GAP = 0.15          # الفراغ بين العلامة والاسم
MARGIN = 0.06


def _shape(font_path: Path, text: str, size: float, tracking: float = 0.0):
    """أشكال الحروف بعد التشكيل: ‏[(مسار SVG، إزاحة س)] وعرض الكلمة."""
    import uharfbuzz as hb
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont

    blob = hb.Blob.from_file_path(str(font_path))
    face = hb.Face(blob)
    font = hb.Font(face)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, {"kern": True, "liga": True})

    tt = TTFont(str(font_path))
    glyph_set = tt.getGlyphSet()
    order = tt.getGlyphOrder()
    scale = size / tt["head"].unitsPerEm

    glyphs, bounds = [], []
    cursor = 0.0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        pen = SVGPathPen(glyph_set)
        # y يُقلب: إحداثيات الخط تصعد، وإحداثيات الصورة تنزل.
        transform = TransformPen(
            pen, (scale, 0, 0, -scale,
                  cursor + pos.x_offset * scale, -pos.y_offset * scale))
        glyph_set[order[info.codepoint]].draw(transform)
        glyphs.append(pen.getCommands())
        bpen = BoundsPen(glyph_set)
        glyph_set[order[info.codepoint]].draw(TransformPen(
            bpen, (scale, 0, 0, -scale,
                   cursor + pos.x_offset * scale, -pos.y_offset * scale)))
        bounds.append(bpen.bounds)
        cursor += pos.x_advance * scale + tracking * size
    return glyphs, cursor - tracking * size, bounds


def _svg_paths(glyph_commands, x: float, baseline: float, fill: str) -> str:
    body = " ".join(c for c in glyph_commands if c)
    return (f'<path transform="translate({x:.2f} {baseline:.2f})" '
            f'fill="{fill}" d="{body}"/>')


def layout(ink, accent, *, full_mark: bool):
    """كل إحداثيات اللوحة، لتُرسَم نقطيًّا ومتّجهًا من حساب واحد."""
    ar_size = HEIGHT * AR_SIZE
    lat_size = HEIGHT * LATIN_SIZE
    ar_glyphs, ar_width, ar_bounds = _shape(ARABIC_FONT, NAME_AR, ar_size)
    lat_glyphs, lat_width, _ = _shape(LATIN_FONT, NAME_LATIN, lat_size,
                                   tracking=LATIN_TRACKING)

    # حدود الحبر الفعلية للكلمة العربية (لا حدود التقدّم) — فوق السطر
    # وتحته — ليُوسَّط الاسم عموديًّا على العلامة بالقياس لا بالتقدير.
    boxes = [b for b in ar_bounds if b]
    tops = [b[1] for b in boxes]
    bottoms = [b[3] for b in boxes]
    ar_top, ar_bottom = min(tops), max(bottoms)

    mark_size = HEIGHT * 0.78
    mark_x = MARGIN * HEIGHT
    text_left = mark_x + mark_size + GAP * HEIGHT
    text_width = max(ar_width, lat_width)
    width = text_left + text_width + MARGIN * HEIGHT

    # كتلة النصّ: العربي ثم Maeen تحته.
    block_gap = HEIGHT * 0.05
    block_height = (ar_bottom - ar_top) + block_gap + lat_size * 0.72
    block_top = (HEIGHT - block_height) / 2
    ar_baseline = block_top - ar_top
    lat_baseline = block_top + (ar_bottom - ar_top) + block_gap + lat_size * 0.72

    return {
        "width": width, "height": float(HEIGHT),
        "mark_x": mark_x, "mark_size": mark_size,
        "ar": (ar_glyphs, text_left + (text_width - ar_width) / 2, ar_baseline),
        "lat": (lat_glyphs, text_left + (text_width - lat_width) / 2,
                lat_baseline),
        "ar_pos": (text_left + (text_width - ar_width) / 2, ar_baseline),
        "lat_pos": (text_left + (text_width - lat_width) / 2, lat_baseline),
        "ink": ink, "accent": accent, "full_mark": full_mark,
    }


def _hex(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb[:3])


def render_png(ink, accent, latin_colour, *, full_mark: bool):
    """نقطيًّا بـPillow — العلامة من ``make_logo`` والاسم من مسارات الخط."""
    from PIL import Image, ImageDraw

    sys.path.insert(0, str(ROOT))
    from tools import make_logo

    plan = layout(ink, accent, full_mark=full_mark)
    super_ = 4
    w, h = int(plan["width"]), int(plan["height"])
    canvas = Image.new("RGBA", (w * super_, h * super_), (0, 0, 0, 0))

    # العلامة
    if full_mark:
        mark = make_logo.render(int(plan["mark_size"] * super_))
        if tuple(ink) != tuple(make_logo.INK):
            raise SystemExit("النسخة الكاملة مرسومة بحبر الشعار الأصلي فقط")
    else:
        mark = make_logo.render_mark(int(plan["mark_size"] * super_),
                                     ink=ink, accent=accent)
    mark = mark.crop(mark.getbbox())
    target_h = int(plan["mark_size"] * super_)
    scale = target_h / mark.height
    mark = mark.resize((max(1, int(mark.width * scale)), target_h),
                       Image.LANCZOS)
    canvas.alpha_composite(
        mark, (int(plan["mark_x"] * super_),
               int((h * super_ - target_h) / 2)))

    # الحروف: Pillow مع raqm تُشكّل العربية بنفسها (اتّصال وصيغ). مواضعها
    # محسوبة من مسارات uharfbuzz نفسها التي يُبنى منها الـSVG.
    from PIL import ImageFont

    ar_font = ImageFont.truetype(
        str(ARABIC_FONT), int(HEIGHT * AR_SIZE * super_),
        layout_engine=ImageFont.Layout.RAQM)
    lat_font = ImageFont.truetype(
        str(LATIN_FONT), int(HEIGHT * LATIN_SIZE * super_),
        layout_engine=ImageFont.Layout.RAQM)
    draw = ImageDraw.Draw(canvas)
    ar_x, ar_base = plan["ar_pos"]
    draw.text((ar_x * super_, ar_base * super_), NAME_AR, font=ar_font,
              fill=tuple(ink), anchor="ls", direction="rtl", language="ar")
    x, base = plan["lat_pos"]
    for char in NAME_LATIN:
        draw.text((x * super_, base * super_), char, font=lat_font,
                  fill=tuple(latin_colour), anchor="ls")
        x += (lat_font.getlength(char) / super_
              + LATIN_TRACKING * HEIGHT * LATIN_SIZE)
    return canvas.resize((w, h), Image.LANCZOS)


def build_svg(ink, accent, latin_colour) -> str:
    plan = layout(ink, accent, full_mark=True)
    w, h = plan["width"], plan["height"]
    ms = plan["mark_size"]
    k = ms / 96.0
    ox, oy = plan["mark_x"], (h - ms) / 2
    ink_hex, acc_hex = _hex(ink), _hex(accent)
    ar_glyphs, ar_x, ar_base = plan["ar"]
    lat_glyphs, lat_x, lat_base = plan["lat"]
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" width="{w:.0f}" height="{h:.0f}" role="img" aria-label="معين — Maeen">
  <title>معين — Maeen</title>
  <!-- العلامة: نفس هندسة assets/logo.svg، مُكبَّرة وموضوعة يسار الاسم. -->
  <g transform="translate({ox:.2f} {oy:.2f}) scale({k:.4f})">
    <circle cx="34" cy="36" r="17.5" fill="none" stroke="{ink_hex}" stroke-width="9"/>
    <path d="M34 58 L34 70 Q34 78 44 78 L88 78" fill="none" stroke="{ink_hex}" stroke-width="9" stroke-linecap="round"/>
    <path d="M29 28 L45 36 L29 44 Z" fill="{acc_hex}"/>
    <rect x="66" y="22" width="22" height="7" rx="3.5" fill="{ink_hex}" opacity="0.30"/>
    <rect x="66" y="36" width="22" height="7" rx="3.5" fill="{ink_hex}" opacity="0.20"/>
    <rect x="66" y="50" width="14" height="7" rx="3.5" fill="{ink_hex}" opacity="0.13"/>
  </g>
  <!-- الاسم: مسارات مشكَّلة من Cairo (SIL OFL) — لا نصّ، فلا يتغيّر بالجهاز. -->
  {_svg_paths(ar_glyphs, ar_x, ar_base, ink_hex)}
  {_svg_paths(lat_glyphs, lat_x, lat_base, _hex(latin_colour))}
</svg>
"""


def main() -> int:
    try:
        import PIL  # noqa: F401
        import uharfbuzz  # noqa: F401
    except ImportError as exc:
        print(f"ينقص: {exc.name} — pip install pillow uharfbuzz",
              file=sys.stderr)
        return 1
    sys.path.insert(0, str(ROOT))
    from tools import make_logo

    out = ROOT / "assets"
    render_png(make_logo.INK, make_logo.ACCENT, make_logo.ACCENT,
               full_mark=True).save(out / "logo_wordmark.png")
    render_png(make_logo.ON_INK, make_logo.ACCENT_LIGHT,
               make_logo.ACCENT_LIGHT,
               full_mark=False).save(out / "logo_wordmark_light.png")
    (out / "logo_wordmark.svg").write_text(
        build_svg(make_logo.INK, make_logo.ACCENT, make_logo.ACCENT),
        encoding="utf-8")
    print("كُتب logo_wordmark.png / logo_wordmark_light.png / logo_wordmark.svg")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    from utils.console import enable_utf8_console

    enable_utf8_console()
    raise SystemExit(main())
