"""علامة باقة «تجربة» المائية: تُطبَّق للتجربة وحدها وعلى كل الصيغ."""
import zipfile
from pathlib import Path

import pytest

from document import trial_mark

docx = pytest.importorskip("docx")


@pytest.fixture
def trial(monkeypatch):
    monkeypatch.setattr(trial_mark, "is_trial", lambda: True)


@pytest.fixture
def paid(monkeypatch):
    monkeypatch.setattr(trial_mark, "is_trial", lambda: False)


def _doc_with_header():
    document = docx.Document()
    document.add_paragraph("نص")
    document.sections[0].different_first_page_header_footer = True
    return document


def test_docx_gets_watermark_in_header_and_first_page(trial, tmp_path):
    document = _doc_with_header()
    assert trial_mark.mark_docx_object(document) is True
    path = tmp_path / "a.docx"
    document.save(str(path))
    with zipfile.ZipFile(path) as archive:
        headers = [n for n in archive.namelist() if n.startswith("word/header")]
        assert len(headers) >= 2                      # عادي + الصفحة الأولى
        for name in headers:
            xml = archive.read(name).decode("utf-8")
            assert "PowerPlusWaterMarkObject" in xml
            assert "MAEEN · TRIAL" in xml
            assert "نسخة تجريبية" in xml
    assert docx.Document(str(path)).paragraphs     # ما زال الملف يُفتح


def test_docx_untouched_for_paid(paid, tmp_path):
    document = _doc_with_header()
    assert trial_mark.mark_docx_object(document) is False
    path = tmp_path / "b.docx"
    document.save(str(path))
    with zipfile.ZipFile(path) as archive:
        assert not any("PowerPlus" in archive.read(n).decode("utf-8", "ignore")
                       for n in archive.namelist() if n.startswith("word/header"))


def test_html_marked_once(trial, tmp_path):
    page = tmp_path / "x.html"
    page.write_text("<html><body><p>مرحبا</p></body></html>", encoding="utf-8")
    assert trial_mark.mark_file(page) is True
    first = page.read_text(encoding="utf-8")
    assert "MAEEN · TRIAL" in first and first.count("data-maeen-trial") == 2
    trial_mark.mark_file(page)                        # لا يتكرّر
    assert page.read_text(encoding="utf-8") == first


def test_html_untouched_for_paid(paid, tmp_path):
    page = tmp_path / "x.html"
    page.write_text("<body></body>", encoding="utf-8")
    assert trial_mark.mark_file(page) is False
    assert page.read_text(encoding="utf-8") == "<body></body>"


def test_zip_html_members_marked(trial, tmp_path):
    package = tmp_path / "s.zip"
    with zipfile.ZipFile(package, "w") as z:
        z.writestr("imsmanifest.xml", "<manifest/>")
        z.writestr("index.html", "<body>hi</body>")
    assert trial_mark.mark_file(package) is True
    with zipfile.ZipFile(package) as z:
        assert z.read("imsmanifest.xml") == b"<manifest/>"
        assert b"MAEEN" in z.read("index.html")


def test_pptx_marked(trial, tmp_path):
    pptx = pytest.importorskip("pptx")
    presentation = pptx.Presentation()
    presentation.slides.add_slide(presentation.slide_layouts[5])
    presentation.slides.add_slide(presentation.slide_layouts[5])
    path = tmp_path / "p.pptx"
    presentation.save(str(path))
    assert trial_mark.mark_file(path) is True
    reopened = pptx.Presentation(str(path))
    for slide in reopened.slides:
        texts = [s.text_frame.text for s in slide.shapes if s.has_text_frame]
        assert "MAEEN · TRIAL" in texts


def test_unknown_suffix_and_missing_file_never_raise(trial, tmp_path):
    assert trial_mark.mark_file(None) is False
    assert trial_mark.mark_file(tmp_path / "nope.pdf") is False
    assert trial_mark.mark_file(tmp_path / "nope.html") is False


def test_tier_from_lease(monkeypatch):
    from licensing import app_gate
    from licensing.lease import Lease

    monkeypatch.setattr(app_gate, "licensing_enabled", lambda: True)

    class FakeStore:
        def read(self):
            return {"lease": "x"}

    monkeypatch.setattr(app_gate, "store", lambda: FakeStore())
    for lease, expected in (
        (Lease(code="C", plan="مفتاحك · سنة", extra={"tier": "trial"}), "trial"),
        (Lease(code="C", plan="تجربة · أسبوع"), "trial"),
        (Lease(code="C", plan="مُدار – فرد · تجربة أسبوع"), ""),
        (Lease(code="C", plan="x", extra={"tier": "managed"}), "managed"),
    ):
        monkeypatch.setattr(app_gate, "verify", lambda text, l=lease: l)
        assert app_gate.current_tier() == expected
        assert app_gate.is_trial() == (expected == "trial")
