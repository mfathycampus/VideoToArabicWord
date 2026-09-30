"""اسم البرنامج «معين» — مصدره وأصوله.

الغاية: ألّا يعود الاسم القديم إلى أي واجهة يراها المستخدم، وألّا تُفقَد
أصول الشعار الجديد من الحزمة (عطلٌ سبق أن رآه المستخدم وحده: شعار يظهر
عند التشغيل من المصدر ويختفي في الحزمة).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NEW_NAME = "معين"
OLD_NAMES = ("محوّل المحاضرات", "محوّل الفيديو إلى Word",
             "محوّل الفيديو إلى مستند Word")

#: ملفات تعرض الاسم للمستخدم. معرّفات التقنية (VideoToArabicWord) خارجها.
USER_FACING = ("app.py", "ui/main_window.py", "packaging/installer.iss",
               "server/worker.js", "assets/logo.svg",
               "assets/logo_mark_small.svg")


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("rel", USER_FACING)
def test_the_old_name_is_gone(rel):
    text = _text(rel)
    for old in OLD_NAMES:
        assert old not in text, f"الاسم القديم «{old}» ما زال في {rel}"


def test_the_app_title_is_the_new_name():
    match = re.search(r'^APP_TITLE = "(.+)"$', _text("app.py"), re.M)
    assert match and match.group(1) == NEW_NAME


def test_the_installer_shows_the_new_name_but_keeps_its_identity():
    """تغيير الاسم لا يغيّر ``AppId`` ولا اسم الملف — وإلا صار التحديث
    تثبيتًا ثانيًا جنب الأول وانقطع مسار الترقية."""
    iss = _text("packaging/installer.iss")
    assert f'#define AppName        "{NEW_NAME}"' in iss
    assert "AppId={{8F3C2A91-6D45-4B7E-9C18-2A5E7D4B1F03}" in iss
    assert '#define AppNameLatin   "VideoToArabicWord"' in iss


def test_the_wordmark_assets_ship_in_the_bundle():
    spec = _text("build.spec")
    assert "logo_wordmark_light.png" in spec and "logo_wordmark.png" in spec


@pytest.mark.parametrize("name", ("logo_wordmark.png",
                                  "logo_wordmark_light.png"))
def test_the_wordmark_png_is_transparent_and_wide(name):
    Image = pytest.importorskip("PIL.Image", reason="Pillow غير مثبَّتة")
    with Image.open(ROOT / "assets" / name) as image:
        assert image.mode == "RGBA"
        assert image.width > image.height * 2, "ليس شعارًا أفقيًّا"
        assert image.getchannel("A").getextrema()[0] == 0, "بلا شفافية"


def test_the_wordmark_svg_carries_the_name_as_paths_not_text():
    """الحروف مسارات: الـSVG يظهر كما هو على جهاز بلا خط Cairo."""
    svg = _text("assets/logo_wordmark.svg")
    assert NEW_NAME in svg            # في العنوان وaria-label
    assert "<text" not in svg
    assert svg.count("<path") >= 4


def test_the_light_wordmark_reads_on_the_dark_header():
    """التباين من البكسلات: أي بكسل ظاهر يجب أن يُقرأ على حبر الشريط."""
    Image = pytest.importorskip("PIL.Image", reason="Pillow غير مثبَّتة")
    from ui import theme
    from tests.test_brand_assets import _contrast, _hex_to_rgb

    back = _hex_to_rgb(theme.INK)
    with Image.open(ROOT / "assets" / "logo_wordmark_light.png") as image:
        pixels = [p for p in image.getdata() if p[3] >= 250]
    assert pixels
    worst = min(_contrast(p, back) for p in pixels)
    assert worst >= 4.5, f"أضعف تباين {worst:.1f}:1"
