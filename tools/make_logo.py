"""يرسم شعار البرنامج نقطيًّا من ``assets/logo.svg``.

    python tools/make_logo.py

**لماذا يُرسَم بـPillow ولا يُحوَّل الـSVG:** تحويل SVG يحتاج
``cairosvg`` أو ``rsvg``، وكلاهما تبعية ثقيلة بمكتبات نظام — لبرنامج
يُشحن للمعلّمين كملف تنفيذي واحد. و Pillow **موجودة أصلًا** (يستعملها
``tools/make_icon.py`` واختيار اللقطات). فالمصدر التحريري هو الـSVG،
وهذا الملف يعيد رسم الشكل نفسه بدائرة ومضلّع ومستطيلات — سبعة أشكال
لا أكثر.

يبقى الـSVG مصدرَ الحقيقة للتصميم؛ من عدّله عدّل الإحداثيات هنا.
والاثنان يُختبَران معًا في ``tests/test_brand_assets.py``.

**ولماذا نُودِع الـPNG في المستودع** بخلاف الـICO: المستند يحتاجه في
كل تشغيل (``document/template.py``)، وتوليده عند التشغيل يعني تبعية
رسم في المسار الحرج. الـICO يُولَّد عند البناء وحده.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:                       # للتلميحات فقط — لا استيراد وقت التشغيل
    from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "assets" / "logo.png"

#: حجم الإخراج. 512 يكفي ترويسة المستند (تُدرَج بعرض 0.95 بوصة) وكل
#: أحجام أيقونة ويندوز التي يشتقّها ``make_icon.py``.
SIZE = 512

#: نرسم بأربعة أضعاف ثم نصغّر: Pillow لا تنعّم الحواف، والتصغير
#: بـLANCZOS هو ما يعطي الحافة الناعمة.
SUPERSAMPLE = 4

INK = (0x0B, 0x2E, 0x2A, 255)        # #0B2E2A — حبر أخضر داكن
ACCENT = (0x0F, 0x76, 0x6E, 255)     # #0F766E — مثلّث التشغيل


def _quad(start, control, end, steps: int = 14):
    """منحنى بيزييه تربيعي — زاوية ذَيل الواو."""
    (x0, y0), (cx, cy), (x1, y1) = start, control, end
    points = []
    for index in range(steps + 1):
        t = index / steps
        u = 1 - t
        points.append((u * u * x0 + 2 * u * t * cx + t * t * x1,
                       u * u * y0 + 2 * u * t * cy + t * t * y1))
    return points


def _round_cap(draw, point, radius, colour):
    """‏Pillow لا تملك نهايات مستديرة للخطوط — نرسمها دائرةً."""
    x, y = point
    draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=colour)


def render_mark(size: int = SIZE) -> "Image.Image":
    """العلامة وحدها — بلا أسطر النصّ، وبسُمك أعرض.

    مقيس لا مُقدَّر: صُغِّر الشعار الكامل إلى 16 و24 بكسل فصارت الأسطر
    ثلاث بقع رمادية لا تُقرأ، واختفى مثلّث التشغيل داخل الحلقة. الحرف
    وحده يبقى مميّزًا، وهو ما تعرضه ويندوز في شريط المهام.
    """
    from PIL import Image, ImageDraw

    scale = size * SUPERSAMPLE / 96.0
    canvas = Image.new("RGBA", (size * SUPERSAMPLE,) * 2, (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    def s(value: float) -> float:
        return value * scale

    stroke = s(12)                     # أعرض: الرفيع يختفي عند التصغير
    draw.ellipse([s(34 - 22), s(36 - 22), s(34 + 22), s(36 + 22)],
                 outline=INK, width=int(round(stroke)))
    tail = ([(s(34), s(58)), (s(34), s(70))]
            + [(s(x), s(y)) for x, y in _quad((34, 70), (34, 78), (44, 78))]
            + [(s(88), s(78))])
    draw.line(tail, fill=INK, width=int(round(stroke)), joint="curve")
    _round_cap(draw, (s(34), s(58)), stroke / 2, INK)
    _round_cap(draw, (s(88), s(78)), stroke / 2, INK)
    draw.polygon([(s(28), s(27)), (s(48), s(36)), (s(28), s(45))],
                 fill=ACCENT)
    return canvas.resize((size, size), Image.LANCZOS)


def render(size: int = SIZE) -> "Image.Image":
    from PIL import Image, ImageDraw

    scale = size * SUPERSAMPLE / 96.0          # الـviewBox 96×96
    canvas = Image.new("RGBA", (size * SUPERSAMPLE,) * 2, (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    def s(value: float) -> float:
        return value * scale

    stroke = s(9)

    # حلقة الواو
    draw.ellipse([s(34 - 22), s(36 - 22), s(34 + 22), s(36 + 22)],
                 outline=INK, width=int(round(stroke)))

    # الذَيل: عمودي، ثم زاوية، ثم أفقي إلى سطر النصّ
    tail = ([(s(34), s(58)), (s(34), s(70))]
            + [(s(x), s(y)) for x, y in _quad((34, 70), (34, 78), (44, 78))]
            + [(s(88), s(78))])
    draw.line(tail, fill=INK, width=int(round(stroke)), joint="curve")
    _round_cap(draw, (s(34), s(58)), stroke / 2, INK)
    _round_cap(draw, (s(88), s(78)), stroke / 2, INK)

    # مثلّث التشغيل داخل الحلقة
    draw.polygon([(s(29), s(28)), (s(47), s(36)), (s(29), s(44))],
                 fill=ACCENT)

    # أسطر النصّ التي يخرج إليها الذيل — شفافية متدرّجة
    # تبدأ عند 66: حافة الحلقة اليمنى عند 60.5 (34+22+نصف السُمك)،
    # فما دونها يتراكب معها. أوّل رسم كان عند 52 وتراكب فعلًا.
    for top, width, opacity in ((22, 22, 0.30), (36, 22, 0.20),
                                (50, 14, 0.13)):
        radius = s(3.5)
        draw.rounded_rectangle(
            [s(66), s(top), s(66 + width), s(top + 7)], radius=radius,
            fill=INK[:3] + (int(255 * opacity),))

    return canvas.resize((size, size), Image.LANCZOS)


def main() -> int:
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("تحتاج Pillow: pip install pillow", file=sys.stderr)
        return 1

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    render().save(TARGET)
    print(f"كُتب {TARGET.relative_to(ROOT)} بحجم {SIZE}×{SIZE}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    from utils.console import enable_utf8_console

    enable_utf8_console()
    raise SystemExit(main())
