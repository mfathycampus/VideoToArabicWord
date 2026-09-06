"""العثور على Tesseract حين لا يكون في PATH.

عطلٌ بلّغ عنه مستخدم فعلي: ثبّت Tesseract كما طُلب منه — بحزمة اللغة
العربية — ثم فتح البرنامج فوجد التحذير كما هو. والسبب أن مثبِّت
UB-Mannheim **لا يؤشّر خيار إضافة PATH افتراضيًّا**، ومن يثبّت بالضغط
على «التالي» — أي كل معلّم — ينتهي بتثبيت سليم غير مرئي لـ
``shutil.which``. سجلّه بعد الإصلاح:

    عُثر على Tesseract خارج PATH: C:\\Program Files\\Tesseract-OCR\\tesseract.exe

أن نطلب من معلّم تحرير متغيّرات البيئة ليعمل البرنامج تحميلٌ لعطبنا
عليه.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

import video.ocr as ocr


@pytest.fixture(autouse=True)
def clear_cache():
    ocr._cached_exe = ""
    yield
    ocr._cached_exe = ""


@pytest.fixture()
def no_path(monkeypatch):
    """‏PATH بلا Tesseract — الحالة التي وقعت على المستخدم."""
    monkeypatch.setattr(ocr.shutil, "which", lambda _name: None)


def test_path_is_still_the_first_source(monkeypatch):
    monkeypatch.setattr(ocr.shutil, "which",
                        lambda name: "/usr/bin/tesseract"
                        if name == "tesseract" else None)
    assert ocr.tesseract_executable() == "/usr/bin/tesseract"


def test_found_in_the_default_windows_location(monkeypatch, no_path, tmp_path):
    """‏Program Files\\Tesseract-OCR — موضع المثبِّت الافتراضي."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    install = tmp_path / "Tesseract-OCR"
    install.mkdir()
    (install / "tesseract.exe").write_text("")

    assert ocr.tesseract_executable() == str(install / "tesseract.exe")
    assert ocr.is_available()


def test_found_in_a_per_user_install(monkeypatch, no_path, tmp_path):
    """التثبيت بلا صلاحيات مدير يذهب إلى LOCALAPPDATA\\Programs."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    install = tmp_path / "Programs" / "Tesseract-OCR"
    install.mkdir(parents=True)
    (install / "tesseract.exe").write_text("")

    assert ocr.tesseract_executable() == str(install / "tesseract.exe")


def test_registry_is_consulted_for_a_custom_path(monkeypatch, no_path, tmp_path):
    """من غيّر مجلد التثبيت لا يجده البحث في المواضع المعتادة."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ocr, "_windows_install_dirs", list)
    custom = tmp_path / "D_Tools" / "Tess"
    custom.mkdir(parents=True)
    (custom / "tesseract.exe").write_text("")
    monkeypatch.setattr(ocr, "_from_windows_registry",
                        lambda: str(custom / "tesseract.exe"))

    assert ocr.tesseract_executable() == str(custom / "tesseract.exe")


def test_absent_everywhere_is_reported_as_absent(monkeypatch, no_path, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    monkeypatch.setattr(ocr, "_from_windows_registry", lambda: None)
    assert ocr.tesseract_executable() is None
    assert not ocr.is_available()


def test_non_windows_does_not_search_windows_paths(monkeypatch, no_path):
    """لا نخترع مسارات ويندوز على لينكس وماك."""
    monkeypatch.setattr(sys, "platform", "linux")
    assert ocr.tesseract_executable() is None


def test_refresh_sees_an_install_made_while_running(monkeypatch, tmp_path):
    """المستخدم يثبّت والبرنامج مفتوح — لا يجوز أن نطلب إعادة تشغيل."""
    calls = {"n": 0}

    def _which(_name):
        calls["n"] += 1
        return None if calls["n"] <= 2 else "/usr/bin/tesseract"

    monkeypatch.setattr(ocr.shutil, "which", _which)
    monkeypatch.setattr(sys, "platform", "linux")

    assert ocr.tesseract_executable() is None
    assert ocr.tesseract_executable() is None, "لم يُخزَّن الفحص"
    assert ocr.refresh() == "/usr/bin/tesseract"


# ── حزمة اللغة العربية ────────────────────────────────────────────────

def _langs(monkeypatch, stdout, returncode=0):
    monkeypatch.setattr(ocr, "tesseract_executable", lambda: "/usr/bin/tesseract")
    monkeypatch.setattr(
        ocr.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, returncode, stdout, ""))


def test_arabic_pack_is_detected(monkeypatch):
    _langs(monkeypatch, "List of available languages (3):\nara\neng\nosd\n")
    assert ocr.languages() == ["ara", "eng", "osd"]
    assert ocr.has_arabic()


def test_missing_arabic_pack_is_detected(monkeypatch):
    """ثنائيّ بلا عربية يقرأ الإنجليزية وحده — عطبٌ أخفى من الغياب."""
    _langs(monkeypatch, "List of available languages (2):\neng\nosd\n")
    assert not ocr.has_arabic()


def test_an_unanswerable_question_is_not_an_accusation(monkeypatch):
    """تعذّر السؤال ≠ العربية غائبة. لا نُحذّر بلا دليل."""
    _langs(monkeypatch, "", returncode=1)
    assert ocr.languages() == []
    assert ocr.has_arabic()
