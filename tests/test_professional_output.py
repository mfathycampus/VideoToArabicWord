"""مخرجٌ يُوزَّع: ما كشفه تشغيل ستّة تسجيلات حقيقية من مجلد PowerSIS.

كل اختبار هنا يقابل عيبًا ظهر في مستندات خطّ الأساس (1.13.1):
    * «اشتركوا في القناة» و«ترجمة نانسي قنقر» مطبوعتان في دليل نظام.
    * صفحة فهرس كاملة تقول «اضغط داخل هذا الإطار ثم F9».
    * شعار ورقم صفحة فوق الغلاف.
    * خطوات الدليل وملاحظاته لا تُصيَّر.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from lxml import etree
from PIL import Image

from audio.text_cleaner import clean_segment_text
from config.schemas import (
    DocumentBlock,
    DocumentPlan,
    DocumentSection,
    VideoMetadata,
)
from document.word_generator import DocumentGenerator

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


# ── هلوسات Whisper المعروفة ─────────────────────────────────────────

def test_known_whisper_hallucinations_are_removed():
    assert clean_segment_text(
        "فقدر يسيب لها تعليق ترجمة نانسي قنقر وعمل سنة") == \
        "فقدر يسيب لها تعليق وعمل سنة"
    assert clean_segment_text("اشتركوا في القناة وعمل سين") == "وعمل سين"
    assert clean_segment_text("شكرا للمشاهدة") == ""
    assert clean_segment_text("لا تنسوا الاشتراك في القناة وتفعيل زر الجرس") == ""


def test_real_channel_talk_survives():
    """«القناة» في Teams قناةٌ حقيقية — لا تُحذف إن عُرّفت."""
    for text in ("اشتركوا في القناة العامة للفصل",
                 "ندخل على القناة التعليمية",
                 "شكرا لكم"):
        assert clean_segment_text(text) == text


# ── المستند ─────────────────────────────────────────────────────────

def _plan(sections: int = 3) -> DocumentPlan:
    def blocks(n):
        return [
            DocumentBlock(kind="step", text=f"اضغط «Admin» في الخطوة {n}.",
                          segment_ids=[n]),
            DocumentBlock(kind="step", text="اختر «Lesson Feedback»."),
            DocumentBlock(kind="note", text="تظهر المعلمات المسكّنات فقط."),
            DocumentBlock(kind="figure", image_id=n, image_filename=f"{n}.jpg",
                          timestamp=float(n), caption="قائمة Admin مفتوحة"),
        ]

    return DocumentPlan(
        title="متابعة تحضير المعلمين", subtitle="دليل إجرائي مصوّر",
        abstract="ملخّص.", generated_by="ai:anthropic/x+vision",
        sections=[DocumentSection(title=f"المهمّة {n}", blocks=blocks(n))
                  for n in range(1, sections + 1)],
        total_segments_in=sections, total_segments_out=sections,
        total_figures_in=sections + 1, total_figures_out=sections,
        figures_omitted=[99])


def _build(tmp_path: Path, plan: DocumentPlan) -> etree._Element:
    for n in range(1, 10):
        Image.new("RGB", (800, 450), (200, 220, 240)).save(tmp_path / f"{n}.jpg")
    metadata = VideoMetadata(
        filename="متابعة تحضير المعلمين.mp4", path=tmp_path / "v.mp4",
        duration_seconds=312.0, width=1920, height=1040, fps=30.0,
        codec="h264", has_audio=True)
    out = tmp_path / "doc.docx"
    DocumentGenerator().generate(plan, metadata, tmp_path, out)
    with zipfile.ZipFile(out) as archive:
        return etree.fromstring(archive.read("word/document.xml"))


def _text(root) -> str:
    return "".join(t.text or "" for t in root.iter(f"{W}t"))


def test_toc_shows_section_titles_not_f9(tmp_path):
    root = _build(tmp_path, _plan())
    text = _text(root)
    assert "F9" not in text
    for n in (1, 2, 3):
        assert f"المهمّة {n}" in text
    # والحقل ما زال حقل TOC يحدّثه Word
    instr = "".join(i.text or "" for i in root.iter(f"{W}instrText"))
    assert "TOC" in instr


def test_steps_are_numbered_per_section_and_notes_rendered(tmp_path):
    root = _build(tmp_path, _plan(sections=3))
    paragraphs = ["".join(t.text or "" for t in p.iter(f"{W}t"))
                  for p in root.iter(f"{W}p")]
    starts = [p.split()[0] for p in paragraphs if "اختر «Lesson" in p
              or "اضغط «Admin» في الخطوة" in p]
    assert starts == ["1.", "2."] * 3
    assert sum(1 for p in paragraphs if p.startswith("ملاحظة:")) == 3


def test_cover_has_no_header_and_uses_document_kind(tmp_path):
    root = _build(tmp_path, _plan())
    sect = next(root.iter(f"{W}sectPr"))
    assert sect.find(f"{W}titlePg") is not None
    text = _text(root)
    assert "دليل إجرائي مصوّر" in text
    assert "ai:anthropic" not in text           # اسم النموذج ليس للقارئ


def test_ppr_children_follow_schema_order(tmp_path):
    """‏pBdr وshd قبل bidi وjc — وإلا عرض Word «محتوى غير قابل للقراءة»."""
    order = ["pStyle", "keepNext", "keepLines", "pageBreakBefore", "widowControl",
             "numPr", "pBdr", "shd", "tabs", "bidi", "spacing", "ind", "jc"]
    root = _build(tmp_path, _plan())
    for ppr in root.iter(f"{W}pPr"):
        tags = [c.tag.replace(W, "") for c in ppr if c.tag.replace(W, "") in order]
        positions = [order.index(t) for t in tags]
        assert positions == sorted(positions), tags


def test_html_renders_steps_and_notes(tmp_path):
    from document.exporters import ExportContext
    from document.exporters.html_export import render_html

    for n in range(1, 5):
        Image.new("RGB", (80, 45)).save(tmp_path / f"{n}.jpg")
    metadata = VideoMetadata(
        filename="v.mp4", path=tmp_path / "v.mp4", duration_seconds=60.0,
        width=1920, height=1080, fps=30.0, codec="h264", has_audio=True)
    html = render_html(ExportContext(
        plan=_plan(1), metadata=metadata, images_dir=tmp_path,
        base_path=tmp_path / "doc"))
    assert html.count('class="step"') == 2
    assert '<span class="n">2</span>' in html
    assert 'class="note"' in html
