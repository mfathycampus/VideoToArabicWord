"""1.19.0 — مصدر كل خطوة: قيلت، أم تُرى، أم استُنتجت.

رُصد (1.15.1، 49 دقيقة): 106 خطوات من 135 بلا أي مرجع، لأن المقاطع كانت
تُنسب إلى القسم وتُلصق بأوّل كتلة فيه. المراجع لا يعرف ما قيل وما استنتجه
النموذج.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from ai.visual_author import VisualAuthor
from config.schemas import DocumentBlock, DocumentPlan, DocumentSection
from document.planner import assert_lossless
from document.quality import evaluate
from tests.test_visual_author import FakeVision, _frames, _transcript

REPLY = {
    "doc_type": "procedure", "title": "إنشاء اختبار في Schoology",
    "abstract": "", "key_points": [],
    "sections": [
        {"title": "إنشاء الاختبار", "blocks": [
            {"type": "step", "text": "اضغط «Add Materials».", "src": {"s": [2], "img": [1]}},
            {"type": "step", "text": "اختر «Test/Quiz».", "src": {"s": [2]}},
            {"type": "step", "text": "اكتب اسم الاختبار.", "src": {"img": [3]}},
            {"type": "step", "text": "احفظ الاختبار.", "src": {}},
            {"type": "figure", "id": 1, "caption": "قائمة Add Materials"},
            {"type": "note", "text": "يظهر الاختبار للطلاب بعد نشره.", "src": {"s": [404]}},
        ]},
        {"title": "ضبط الإعدادات", "blocks": [
            {"type": "step", "text": "افتح «Settings»."},       # بلا src، تليه لقطته
            {"type": "figure", "id": 3, "caption": "صفحة الإعدادات"},
            {"type": "step", "text": "فعّل «Randomize».", "s": [3]},   # صيغة مسطّحة
        ]},
    ],
    "omit": [2],
}


def _plan(tmp_path, reply=REPLY):
    frames = _frames(tmp_path, 3)
    transcript = _transcript([(1.0, "نبدأ"), (8.0, "نضغط اد ماتيريالز ونختار تست"),
                              (25.0, "نفعل الخلط العشوائي")])
    fake = FakeVision([reply])
    plan = VisualAuthor(fake).build_plan(transcript, frames, tmp_path, "اختبار")
    return plan, fake


def _blocks(plan):
    return [b for s in plan.sections for b in s.blocks if b.kind in ("step", "note")]


def test_speech_is_numbered_so_the_model_can_cite_it(tmp_path):
    _plan_, fake = _plan(tmp_path)
    system, parts = fake.calls[0]
    text = " ".join(p.get("text", "") for p in parts)
    assert "[#2 · 00:00:08]" in text
    assert '"src"' in system


def test_each_step_carries_its_verified_source(tmp_path):
    plan, _ = _plan(tmp_path)
    got = [(b.text[:12], b.source, b.evidence_segments, b.evidence_images)
           for b in _blocks(plan)]
    assert got == [
        ("اضغط «Add Ma", "both", [2], [1]),
        ("اختر «Test/Q", "transcript", [2], []),
        ("اكتب اسم الا", "screen", [], [3]),
        ("احفظ الاختبا", "inferred", [], []),
        # مقطعٌ لم يُرسَل (404) ليس دليلًا
        ("يظهر الاختبا", "inferred", [], []),
        # بلا src وتليه لقطته مباشرةً: مصدره الشاشة بنصّ التعليمة
        ("افتح «Settin", "screen", [], [3]),
        ("فعّل «Random", "transcript", [3], []),
    ]


def test_evidence_does_not_break_the_one_segment_once_rule(tmp_path):
    """مقطع #2 دليلُ خطوتين — ولا يتكرّر في ``segment_ids`` (ADR-010)."""
    plan, _ = _plan(tmp_path)
    assert_lossless(plan)
    assert evaluate(plan).violations == ()


def test_quality_counts_steps_by_source_and_warns_on_inferred(tmp_path):
    plan, _ = _plan(tmp_path)
    report = evaluate(plan, segments=_transcript([(1.0, "نبدأ")]).segments,
                      duration_seconds=40.0)
    assert report.diagnostics["steps_by_source"] == {
        "both": 1, "transcript": 2, "screen": 2, "inferred": 2}
    assert report.diagnostics["sourced_steps_pct"] == pytest.approx(71.4)
    assert any("2 من 7 خطوة وملاحظة لا يسندها" in w for w in report.warnings)


def test_plans_without_sources_report_none_not_zero():
    plan = DocumentPlan(title="t", sections=[DocumentSection(
        title="قسم", blocks=[DocumentBlock(kind="step", text="اضغط.", segment_ids=[1])])])
    d = evaluate(plan).diagnostics
    assert d["steps_by_source"] == {} and d["sourced_steps_pct"] is None


def test_inferred_steps_are_marked_in_html():
    from document.exporters.html_export import _render_block

    inferred = DocumentBlock(kind="step", text="احفظ.", source="inferred")
    sourced = DocumentBlock(kind="step", text="اضغط.", source="transcript")
    note = DocumentBlock(kind="note", text="تنبيه.", source="inferred")
    assert "مقترحة — راجعها" in _render_block(inferred, Path("."), False, 1)
    assert "مقترحة" not in _render_block(sourced, Path("."), False, 2)
    assert "مقترحة — راجعها" in _render_block(note, Path("."), False, 2)


def test_inferred_steps_are_marked_in_word(tmp_path):
    from docx import Document

    from document.word_generator import DocumentGenerator

    from document.template import build_styles

    gen = DocumentGenerator()
    doc = Document()
    build_styles(doc, gen.theme)
    gen._add_step(doc, "احفظ الاختبار.", 1, inferred=True)
    gen._add_step(doc, "اضغط «Add Materials».", 2)
    texts = [p.text for p in doc.paragraphs]
    assert "مقترحة — راجعها" in texts[0] and "مقترحة" not in texts[1]


def test_section_with_cited_speech_is_not_reported_as_unsourced():
    """تشغيل 1.19.0 الحقيقي: «إنشاء مجلد» يبدأ عند لقطته، وكلامه قيل قبلها
    فوُزّع على القسم السابق — وأُنذر به خطأً «مصدره الشاشة وحدها»."""
    plan = DocumentPlan(title="t", generated_by="ai:anthropic/x", sections=[
        DocumentSection(title="نشر خبر", blocks=[
            DocumentBlock(kind="step", text="اضغط.", segment_ids=[1, 2])]),
        DocumentSection(title="إنشاء مجلد لتنظيم المحتوى", blocks=[
            DocumentBlock(kind="step", text="اختر «إنشاء مجلد».", source="both",
                          evidence_segments=[2], evidence_images=[14]),
            DocumentBlock(kind="figure", image_id=14, image_filename="14.jpg")]),
        DocumentSection(title="متطلبات النشر", blocks=[
            DocumentBlock(kind="bullets", bullets=["خبر أسبوعي"], source="transcript",
                          evidence_segments=[1])]),
    ])
    report = evaluate(plan)
    assert report.diagnostics["sections_without_source"] == []
    assert not any(w.startswith("provenance") for w in report.warnings)
