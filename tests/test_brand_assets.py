"""هوية البرنامج — أصولها وألوانها ومصدرها الواحد.

سبب وجود هذا الملف: كان ``assets/logo.png`` شعار **مدرسة** (منارات
الرياض)، و``document/template.py`` يسمّيه «شعار المشروع» ويأخذ منه
لونه. فكان كل مستند يخرج بعلامة جهة بعينها — خطأ داخلها لأنه يخلط
هوية أداة بهوية مؤسّسة، وخطأ خارجها لأنه انتحال.

الاختبارات هنا تمنع عودة ذلك، وتثبّت أن الواجهة والمستند والأيقونة
تشتقّ ألوانها من مصدر واحد.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from ui import theme

ROOT = Path(__file__).resolve().parents[1]
LOGO_PNG = ROOT / "assets" / "logo.png"
LOGO_SVG = ROOT / "assets" / "logo.svg"
LOGO_LIGHT = ROOT / "assets" / "logo_light.png"

#: كحليّ شعار المدرسة. لا يجوز أن يعود إلى أي مسار في البرنامج.
SCHOOL_NAVY = "2D2E82"


def test_the_program_ships_its_own_logo():
    assert LOGO_PNG.is_file(), "لا شعار — ترويسة المستند تخرج فارغة"
    assert LOGO_SVG.is_file(), "المصدر المتّجه مفقود، فلا يمكن تعديل الشعار"


def test_the_school_navy_is_gone_from_the_theme():
    """اللون نفسه دليلٌ على عودة الشعار."""
    assert SCHOOL_NAVY.lower() not in theme.INK.lower()
    assert SCHOOL_NAVY.lower() not in theme.ACCENT.lower()


def test_the_school_navy_is_gone_from_the_document():
    from document.template import Theme

    assert Theme().accent_hex.upper() != SCHOOL_NAVY


def test_document_and_interface_share_the_logo_colours():
    """مصدر واحد: ما يُرى في النافذة هو ما يُطبع في المستند."""
    from document.template import Theme

    document = Theme()
    assert f"#{document.accent_hex}".lower() == theme.INK.lower()
    soft = document.accent_soft
    assert f"#{soft[0]:02x}{soft[1]:02x}{soft[2]:02x}" == theme.ACCENT.lower()


def test_the_logo_is_square_and_transparent():
    """ترويسة المستند وأيقونة ويندوز تفترضان مربّعًا بخلفية شفّافة."""
    Image = pytest.importorskip("PIL.Image", reason="Pillow غير مثبَّتة")

    with Image.open(LOGO_PNG) as image:
        assert image.width == image.height, "شعار غير مربّع يُمطَّط في ويندوز"
        assert image.mode == "RGBA", "بلا شفافية يظهر مربّع أبيض حول الشعار"
        assert image.width >= 256, "أصغر من أن يُشتقّ منه حجم 256 للأيقونة"


def test_the_svg_and_the_renderer_agree_on_colour():
    """المصدر المتّجه ومولّد النقطية يجب ألّا يفترقا بصمت."""
    from tools.make_logo import ACCENT, INK

    svg = LOGO_SVG.read_text(encoding="utf-8")
    assert f"#{INK[0]:02X}{INK[1]:02X}{INK[2]:02X}" in svg.upper()
    assert f"#{ACCENT[0]:02X}{ACCENT[1]:02X}{ACCENT[2]:02X}" in svg.upper()


def test_the_small_mark_drops_the_text_lines():
    """قياس: عند 16 و24 بكسل تصير أسطر النصّ بقعًا رمادية لا تُقرأ."""
    pytest.importorskip("PIL.Image")
    from tools.make_logo import render, render_mark

    full = render(64)
    mark = render_mark(64)
    # منطقة الأسطر (يمين الشعار) شبه فارغة في النسخة المختصرة
    box = (44, 10, 64, 44)
    assert mark.crop(box).getbbox() is None
    assert full.crop(box).getbbox() is not None


def test_the_stylesheet_uses_the_theme_not_literals():
    """ورقة الأنماط تُبنى من الثوابت — لا ألوان مكتوبة بيدها."""
    sheet = theme.stylesheet()
    assert theme.ACCENT in sheet and theme.INK in sheet
    literals = set(re.findall(r"#[0-9A-Fa-f]{6}", sheet))
    known = {c.upper() for c in (
        theme.INK, theme.ACCENT, theme.ACCENT_DARK, theme.MUTED,
        theme.BORDER, theme.BORDER_SOFT, theme.SURFACE, theme.BACKGROUND)}
    unknown = {c for c in literals if c.upper() not in known}
    assert unknown <= {"#FFFFFF"}, f"ألوان خارج الثيم: {unknown}"


# ── الشعار فوق الأرضيّة الداكنة ───────────────────────────────────────
#
# عطلٌ رآه المستخدم ولم يره أي اختبار: شريط الواجهة العلوي أرضيّته
# ``theme.INK``، والشعار مرسوم بـ``theme.INK`` نفسه. أي أن البرنامج كان
# يرسم شعاره بلون أرضيّته — فلا يظهر منه إلا مثلّث التشغيل باهتًا.
# «واجهة البرنامج اللوجو غير واضح».
#
# ولذلك تقيس هذه الاختبارات **التباين من البكسلات نفسها** لا من أسماء
# الثوابت: لو عاد أحدهم فرسم النسخة الفاتحة بالحبر الداكن لمرّ أي فحص
# اسمي، وسقط هذا.


def _relative_luminance(rgb) -> float:
    """‏WCAG 2.1 §رياضيات التباين."""
    def channel(value: float) -> float:
        value /= 255.0
        return value / 12.92 if value <= 0.03928 else \
            ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(front, back) -> float:
    a, b = _relative_luminance(front), _relative_luminance(back)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def _hex_to_rgb(value: str):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def test_the_dark_logo_would_be_invisible_on_the_header():
    """توثيق العطل: هذا بالضبط ما كان يفعله ``_build_header``."""
    assert _contrast(_hex_to_rgb(theme.INK), _hex_to_rgb(theme.INK)) < 1.1


def test_a_light_logo_ships_for_the_dark_header():
    assert LOGO_LIGHT.is_file(), "بلا نسخة فاتحة يختفي الشعار في الشريط"


def test_every_visible_pixel_of_the_light_logo_reads_on_the_ink():
    """التباين مقيس من البكسلات لا من أسماء الثوابت.

    الحدّ 4.5:1 — حدّ WCAG AA للنصّ العادي. العلامة أكبر من حرف، لكن
    ما دون ذلك يعني أنها ستبهت على شاشة لابتوب رخيصة في قاعة مضاءة.
    """
    Image = pytest.importorskip("PIL.Image", reason="Pillow غير مثبَّتة")

    background = _hex_to_rgb(theme.INK)
    with Image.open(LOGO_LIGHT) as image:
        pixels = image.convert("RGBA").getdata()

    # البكسلات المعتِمة وحدها: حواف التنعيم شبه شفّافة بطبيعتها.
    solid = [p for p in pixels if p[3] > 200]
    assert solid, "الصورة فارغة"
    worst = min(_contrast(p, background) for p in solid)
    assert worst >= 4.5, f"أضعف تباين في الشعار الفاتح {worst:.1f}:1"


def test_the_header_uses_the_light_logo():
    """الحارس على الملفّ لا على الصورة: تبديل السطر يُسقطه."""
    source = (ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    header = source.split("def _build_header")[1].split("def ")[0]
    assert "logo_light.png" in header


def test_the_packaged_build_ships_the_light_logo():
    """شحن الداكن وحده يعني شعارًا يظهر من المصدر ويختفي في الحزمة —
    عطلٌ لا يراه إلا المستخدم النهائي."""
    spec = (ROOT / "build.spec").read_text(encoding="utf-8")
    assert "logo_light.png" in spec


def test_the_play_triangle_stays_inside_the_ring():
    """‏Pillow ترسم حدّ الدائرة إلى الداخل، فالسُمك يقضم نصف القطر.

    عند سُمك 12 يصير الداخل 10 بينما رأس المثلّث على 14 — يخترق الحلقة
    فيصير الشكل بقعةً لا حرفًا. الحساب هنا هندسي لا بصري.
    """
    from math import hypot

    from tools.make_logo import (FULL_STROKE, FULL_TRIANGLE, MARK_STROKE,
                                 RING_CENTRE, RING_RADIUS, TRIANGLE)

    cx, cy = RING_CENTRE
    for stroke, triangle, name in ((MARK_STROKE, TRIANGLE, "المختصرة"),
                                   (FULL_STROKE, FULL_TRIANGLE, "الكاملة")):
        inner = RING_RADIUS - stroke
        worst = max(hypot(x - cx, y - cy) for x, y in triangle)
        assert worst < inner, (
            f"النسخة {name}: المثلّث يبلغ {worst:.1f} والداخل {inner:.1f}")


def test_the_svg_and_the_renderer_agree_on_geometry():
    """المصدر المتّجه ومولّد النقطية افترقا مرّة بصمت.

    ‏SVG يرسم حدّ الدائرة على **منتصف** المسار، وPillow ترسمه إلى
    **الداخل**. فـ``r=22 stroke=12`` في SVG حلقةٌ حافتها 28، وفي Pillow
    حافتها 22 — شكلان مختلفان من ملفّين يُفترض أنهما شيء واحد. النصّ
    هنا يقارن الحافة الخارجية لا نصف القطر.
    """
    from tools.make_logo import (FULL_STROKE, MARK_STROKE, RING_CENTRE,
                                 RING_RADIUS, TRIANGLE, FULL_TRIANGLE)

    cases = ((LOGO_SVG, FULL_STROKE, FULL_TRIANGLE),
             (ROOT / "assets" / "logo_mark_small.svg", MARK_STROKE, TRIANGLE))
    for path, stroke, triangle in cases:
        svg = path.read_text(encoding="utf-8")
        radius = float(re.search(r'\br="([\d.]+)"', svg).group(1))
        width = float(re.search(r'stroke-width="([\d.]+)"', svg).group(1))
        assert width == stroke, f"{path.name}: سُمك مختلف"
        assert radius + width / 2 == RING_RADIUS, (
            f"{path.name}: الحافة الخارجية {radius + width / 2} "
            f"لا {RING_RADIUS}")

        cx, cy = RING_CENTRE
        assert f'"{cx:g}"' in svg and f'"{cy:g}"' in svg
        drawn = re.search(r'<path d="M([\d. LZ]+)" fill="#0F766E"', svg)
        assert drawn, f"{path.name}: لا مثلّث"
        numbers = [float(n) for n in re.findall(r"[\d.]+", drawn.group(1))]
        assert numbers == [c for point in triangle for c in point]
