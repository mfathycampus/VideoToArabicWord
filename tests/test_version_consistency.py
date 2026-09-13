"""رقم الإصدار: مصدرٌ واحد، وحارسٌ على من يفترق عنه.

سبب وجود هذا الملف عطلٌ لاحظه المستخدم ولم ألاحظه أنا: وسمتُ الإصدار
``v1.5.0`` فخرج المثبِّت باسم ``VideoToArabicWord-1.5.0-setup.exe``،
بينما ``version.py`` ما زال ``1.4.0`` — فترويسة البرنامج تعرض 1.4.0.
رقمان لشيء واحد، والمستخدم هو من رآهما يفترقان.

وهو الدرس نفسه الذي أنتج ``document/titles.py`` و
``_source_duration_seconds``: تعريفان لشيء واحد يفترقان بصمت. والفرق
هنا أن أحد التعريفين — الوسم — يعيش خارج المستودع، فلا يمسّه اختبار.
لذلك الحراسة على طبقتين:

* هنا: سجلّ التغييرات يجب أن يذكر الإصدار الحالي. لا يمكن أن يُرفع
  الرقم بلا أن يُكتب ما تغيّر فيه.
* في ``build.yml``: خطوة تُسقط البناء الموسوم إن اختلف الوسم عن
  ``version.py`` — قبل أن يُنتَج مثبِّت يحمل التناقض.
"""
from __future__ import annotations

import re
from pathlib import Path

from version import APP_VERSION

ROOT = Path(__file__).resolve().parents[1]
_HEADING = re.compile(r"^## (\d+\.\d+\.\d+)", re.M)


def _headings() -> list[str]:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    return _HEADING.findall(text)


def test_the_version_is_a_plain_three_part_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+", APP_VERSION), APP_VERSION


def test_the_changelog_documents_the_current_version():
    """رفعُ الرقم بلا كتابة ما تغيّر يعني إصدارًا لا أحد يعرف ما فيه."""
    assert APP_VERSION in _headings(), (
        f"لا مدخل في CHANGELOG للإصدار {APP_VERSION}")


def test_the_newest_changelog_entry_is_the_current_version():
    """أحدث مدخل هو الإصدار الحالي — وإلا فالرقم متأخّر عن العمل."""
    headings = _headings()
    assert headings, "لا عناوين إصدارات في CHANGELOG"
    assert headings[0] == APP_VERSION, (
        f"أحدث مدخل {headings[0]} والإصدار {APP_VERSION}")


def test_the_tagged_build_checks_the_tag_against_version_py():
    """الحارس الذي لا يستطيع اختبارٌ محلّي تنفيذه — نتحقّق من وجوده.

    الوسم يعيش خارج المستودع، فلا سبيل لمقارنته هنا. ما يمكن ضمانه أن
    الخطوة التي تقارنه لم تُحذف من مسار البناء.
    """
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(
        encoding="utf-8")
    assert "version.APP_VERSION" in workflow, (
        "سقطت خطوة مطابقة الوسم — يعود المثبِّت يحمل رقمًا والبرنامج آخر")
    # وقبل بناء المثبِّت لا بعده
    assert (workflow.index("version.APP_VERSION")
            < workflow.index("بناء المثبِّت"))


# ── التعريف الثالث: pyproject.toml ────────────────────────────────────
# بعد إصلاح الوسم والسجلّ بقي ملفٌّ ثالث يحمل الرقم بيده: ``pyproject``
# كان يقول ``1.4.0`` والبرنامج ``1.5.0``. الحارسان السابقان لم يمسّاه
# لأن كلًّا منهما وُضع لموضع بعينه لا لقاعدة عامّة. والقاعدة هي: رقم
# الإصدار يُقرأ من ``version.py`` ولا يُكتب في مكان ثانٍ أبدًا.

_PYPROJECT = ROOT / "pyproject.toml"
_STATIC_VERSION = re.compile(r"^version\s*=\s*[\"'](\d+\.\d+\.\d+)", re.M)


def test_pyproject_does_not_write_the_number_by_hand():
    text = _PYPROJECT.read_text(encoding="utf-8")
    hardcoded = _STATIC_VERSION.findall(text)
    assert not hardcoded, (
        f"pyproject.toml يكتب الرقم بيده ({hardcoded}) — "
        "استعمل dynamic + attr على version.APP_VERSION")


def test_pyproject_takes_its_version_from_version_py():
    text = _PYPROJECT.read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in text, (
        "‏pyproject لا يعلن الإصدار ديناميكيًّا")
    assert "version.APP_VERSION" in text, (
        "‏pyproject لا يشتقّ الإصدار من version.APP_VERSION")


def test_the_version_module_ships_with_the_distribution():
    """‏``attr:`` لا يجد وحدةً غير مُدرَجة — والتوزيعة تخرج بلا رقم."""
    text = _PYPROJECT.read_text(encoding="utf-8")
    assert 'py-modules = ["version"]' in text
