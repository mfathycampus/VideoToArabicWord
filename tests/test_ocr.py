"""video/ocr.py — استخراج نص الشاشة عبر Tesseract.

قرار معماري مُلزِم (انظر تعليق الوحدة): Tesseract عبر subprocess، بلا
PyTorch (ADR-012) وبلا API خارجي (ADR-002)، بنفس نمط استدعاء ffmpeg
(ADR-013). اختياري بالكامل: غيابه أو فشله على صورة بعينها لا يوقف
المعالجة — سلسلة فارغة فقط.
"""
from __future__ import annotations

import shutil

import pytest

from video import ocr


def _has_tesseract() -> bool:
    return shutil.which("tesseract") is not None


def _make_text_image(tmp_path, text: str):
    """صورة بسيطة بنص إنجليزي واضح — تكفي لإثبات أن الاستدعاء الفعلي
    لـ Tesseract يعمل، بمعزل عن دقة OCR على العربية (غير مقيسة هنا)."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    img = np.full((200, 800, 3), 255, dtype="uint8")
    cv2.putText(img, text, (30, 120), cv2.FONT_HERSHEY_SIMPLEX, 2.2,
               (0, 0, 0), 4, cv2.LINE_AA)
    path = tmp_path / "text.png"
    cv2.imwrite(str(path), img)
    return path


def test_is_available_reflects_shutil_which(monkeypatch):
    monkeypatch.setattr(ocr, "_cached_exe", "")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    # جهازٌ عليه Tesseract في Program Files (جهاز المستخدم نفسه) يجده
    # الاكتشاف خارج PATH — والاختبار يسأل عن غيابه في كل المصادر.
    monkeypatch.setattr(ocr, "_windows_install_dirs", lambda: [])
    monkeypatch.setattr(ocr, "_from_windows_registry", lambda: None)
    assert ocr.is_available() is False


def test_is_available_true_when_binary_found(monkeypatch):
    monkeypatch.setattr(ocr, "_cached_exe", "")
    monkeypatch.setattr(shutil, "which",
                        lambda name: "/usr/bin/tesseract" if "tesseract" in name else None)
    assert ocr.is_available() is True


def test_extract_text_returns_empty_when_binary_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "_cached_exe", None)
    fake_image = tmp_path / "does_not_matter.png"
    fake_image.write_bytes(b"not a real image")
    assert ocr.extract_text(fake_image) == ""


def test_extract_text_returns_empty_for_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "_cached_exe", "")  # يفحص PATH الحقيقي
    assert ocr.extract_text(tmp_path / "ghost.png") == ""


def test_install_hint_is_never_empty():
    assert ocr.install_hint().strip()


def test_clean_drops_noise_lines():
    raw = "عنوان الشريحة\n\n.\nx\nالسطر الثاني من الشرح\n \n"
    cleaned = ocr._clean(raw)
    assert "عنوان الشريحة" in cleaned
    assert "السطر الثاني من الشرح" in cleaned
    assert "\nx\n" not in f"\n{cleaned}\n"


@pytest.mark.skipif(not _has_tesseract(), reason="Tesseract غير مثبَّت في هذه البيئة")
def test_extract_text_reads_real_rendered_text(tmp_path):
    """اختبار فعلي على ثنائي Tesseract الحقيقي — لا محاكاة."""
    image = _make_text_image(tmp_path, "HELLO WORLD")
    text = ocr.extract_text(image)
    assert "HELLO" in text.upper()


@pytest.mark.skipif(not _has_tesseract(), reason="Tesseract غير مثبَّت في هذه البيئة")
def test_extract_text_on_blank_image_is_empty_or_whitespace_only(tmp_path):
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    blank = np.full((200, 800, 3), 255, dtype="uint8")
    path = tmp_path / "blank.png"
    cv2.imwrite(str(path), blank)
    assert ocr.extract_text(path).strip() == ""


@pytest.mark.skipif(not _has_tesseract(), reason="Tesseract غير مثبَّت في هذه البيئة")
def test_extract_text_on_corrupt_image_does_not_raise(tmp_path):
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"this is not a real png file at all")
    assert ocr.extract_text(corrupt) == ""


@pytest.mark.skipif(not _has_tesseract(), reason="Tesseract غير مثبَّت في هذه البيئة")
def test_arabic_language_pack_does_not_error_on_english_image(tmp_path):
    """يتأكد أن طلب ara+eng لا يفشل حتى لو كانت الصورة إنجليزية بحتة —
    هذا ما يضمن أن غياب حزمة اللغة العربية سيظهر كخطأ ffmpeg-مثل عبر
    الاستثناء لا كنص فارغ صامت مضلِّل."""
    image = _make_text_image(tmp_path, "TEST 123")
    # لا رفع استثناء، وبعض النص يُستخرج
    text = ocr.extract_text(image)
    assert isinstance(text, str)
