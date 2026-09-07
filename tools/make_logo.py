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

#: نسخة فاتحة للأرضيّات الداكنة. سببها عطلٌ رآه المستخدم: شريط
#: الواجهة العلوي أرضيّته ``#0B2E2A`` — وهو **لون حبر الشعار نفسه**.
#: فكان الشعار يُرسم بلون أرضيّته، أي لا يُرى إطلاقًا. تفتيحُه بمرشّح
#: عند العرض غير ممكن في ``QPixmap`` بلا رسمٍ يدوي، فنُودِع نسخة.
LIGHT_TARGET = ROOT / "assets" / "logo_light.png"

#: حجم الإخراج. 512 يكفي ترويسة المستند (تُدرَج بعرض 0.95 بوصة) وكل
#: أحجام أيقونة ويندوز التي يشتقّها ``make_icon.py``.
SIZE = 512

#: نرسم بأربعة أضعاف ثم نصغّر: Pillow لا تنعّم الحواف، والتصغير
#: بـLANCZOS هو ما يعطي الحافة الناعمة.
SUPERSAMPLE = 4

INK = (0x0B, 0x2E, 0x2A, 255)        # #0B2E2A — حبر أخضر داكن
ACCENT = (0x0F, 0x76, 0x6E, 255)     # #0F766E — مثلّث التشغيل

#: ألوان النسخة الفاتحة، وهي **مقيسة على الأرضيّة الداكنة** لا مختارة:
#:     ‏#F7FAF9 على #0B2E2A  →  14.0:1  (يتجاوز AAA)
#:     ‏#5EEAD4 على #0B2E2A  →   9.9:1  (يتجاوز AAA)
#: و``#0F766E`` — لون المثلّث الأصلي — يعطي 1.9:1 على الأرضيّة نفسها،
#: أي أقلّ من نصف الحدّ الأدنى. لذلك لا يكفي قلبُ الحبر وحده.
ON_INK = (0xF7, 0xFA, 0xF9, 255)     # #F7FAF9
ACCENT_LIGHT = (0x5E, 0xEA, 0xD4, 255)  # #5EEAD4

# ── هندسة الشكل، بإحداثيات الـviewBox 96×96 ──────────────────────────
#
# ثوابت مسمّاة لا أرقامًا داخل النداءات، لأن بينها علاقةً يجب أن تصمد:
# ‏**Pillow ترسم حدّ الدائرة إلى الداخل**، فنصف القطر الداخلي هو
# ``RING_RADIUS - stroke`` — ورأس المثلّث يجب أن يبقى دونه. تجاهُل هذه
# العلاقة هو ما جعل المثلّث يخترق الحلقة فيصير الشكل بقعةً لا حرفًا،
# وهو نصف شكوى «اللوجو غير واضح». يحرسها ``tests/test_brand_assets.py``.
RING_CENTRE = (34.0, 36.0)
RING_RADIUS = 22.0

#: سُمك النسخة الكاملة (مع أسطر النصّ) والنسخة المختصرة. المختصرة
#: أعرض لأنها تُصغَّر إلى 16–34 بكسل، والرفيع يختفي هناك.
FULL_STROKE = 9.0
MARK_STROKE = 11.0

#: مثلّث التشغيل داخل الحلقة — لكل سُمك مثلّثه، لأن السُمك يحدّد
#: المساحة الداخلية المتاحة.
FULL_TRIANGLE = ((29.0, 28.0), (45.0, 36.0), (29.0, 44.0))
TRIANGLE = ((29.0, 29.0), (43.0, 36.0), (29.0, 43.0))


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


def render_mark(size: int = SIZE, ink=INK, accent=ACCENT) -> "Image.Image":
    """العلامة وحدها — بلا أسطر النصّ، وبسُمك أعرض.

    مقيس لا مُقدَّر: صُغِّر الشعار الكامل إلى 16 و24 بكسل فصارت الأسطر
    ثلاث بقع رمادية لا تُقرأ، واختفى مثلّث التشغيل داخل الحلقة. الحرف
    وحده يبقى مميّزًا، وهو ما تعرضه ويندوز في شريط المهام — وهو أيضًا
    ما يصلح لشريط الواجهة العلوي بارتفاع 34 بكسل، وهو في المدى نفسه.

    ‏``ink`` و``accent`` وسيطان لتُبنى النسخة الفاتحة من **نفس** رسم
    النسخة الداكنة: نسخةٌ ثانية مرسومة بيدها تفترق عند أوّل تعديل.
    """
    from PIL import Image, ImageDraw

    scale = size * SUPERSAMPLE / 96.0
    canvas = Image.new("RGBA", (size * SUPERSAMPLE,) * 2, (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    def s(value: float) -> float:
        return value * scale

    stroke = s(MARK_STROKE)
    cx, cy = RING_CENTRE
    draw.ellipse([s(cx - RING_RADIUS), s(cy - RING_RADIUS),
                  s(cx + RING_RADIUS), s(cy + RING_RADIUS)],
                 outline=ink, width=int(round(stroke)))
    tail = ([(s(34), s(58)), (s(34), s(70))]
            + [(s(x), s(y)) for x, y in _quad((34, 70), (34, 78), (44, 78))]
            + [(s(88), s(78))])
    draw.line(tail, fill=ink, width=int(round(stroke)), joint="curve")
    _round_cap(draw, (s(34), s(58)), stroke / 2, ink)
    _round_cap(draw, (s(88), s(78)), stroke / 2, ink)
    draw.polygon([(s(x), s(y)) for x, y in TRIANGLE], fill=accent)
    return canvas.resize((size, size), Image.LANCZOS)


def render(size: int = SIZE) -> "Image.Image":
    from PIL import Image, ImageDraw

    scale = size * SUPERSAMPLE / 96.0          # الـviewBox 96×96
    canvas = Image.new("RGBA", (size * SUPERSAMPLE,) * 2, (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    def s(value: float) -> float:
        return value * scale

    stroke = s(FULL_STROKE)
    cx, cy = RING_CENTRE

    # حلقة الواو
    draw.ellipse([s(cx - RING_RADIUS), s(cy - RING_RADIUS),
                  s(cx + RING_RADIUS), s(cy + RING_RADIUS)],
                 outline=INK, width=int(round(stroke)))

    # الذَيل: عمودي، ثم زاوية، ثم أفقي إلى سطر النصّ
    tail = ([(s(34), s(58)), (s(34), s(70))]
            + [(s(x), s(y)) for x, y in _quad((34, 70), (34, 78), (44, 78))]
            + [(s(88), s(78))])
    draw.line(tail, fill=INK, width=int(round(stroke)), joint="curve")
    _round_cap(draw, (s(34), s(58)), stroke / 2, INK)
    _round_cap(draw, (s(88), s(78)), stroke / 2, INK)

    # مثلّث التشغيل داخل الحلقة
    draw.polygon([(s(x), s(y)) for x, y in FULL_TRIANGLE], fill=ACCENT)

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
    # مقصوصة على حدود الرسم لا مربّعة: شريط الواجهة يُصغّرها
    # **بالارتفاع**، والهوامش الشفّافة في المربّع تأكل نحو ثُلث الارتفاع
    # فتخرج العلامة أصغر مما يسمح به الشريط. الأيقونة وحدها هي التي
    # تحتاج مربّعًا (ويندوز يمطّط غيره).
    light = render_mark(ink=ON_INK, accent=ACCENT_LIGHT)
    light.crop(light.getbbox()).save(LIGHT_TARGET)
    print(f"كُتب {LIGHT_TARGET.relative_to(ROOT)} بحجم {SIZE}×{SIZE}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    from utils.console import enable_utf8_console

    enable_utf8_console()
    raise SystemExit(main())
