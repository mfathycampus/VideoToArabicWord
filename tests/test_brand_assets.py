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
