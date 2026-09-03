"""المخطِّط: بنية الوثيقة وضمانات عدم الضياع والتكرار (ADR-010)."""
import pytest

from config.schemas import (
    AudioSegment, DocumentPlan, DocumentSection, DocumentBlock,
    KeyframeMetadata, TranscriptionResult,
)
from document.planner import TimelinePlanner, assert_lossless


def kf(image_id, ts):
    return KeyframeMetadata(image_id=image_id, timestamp=ts,
                            filename=f"{image_id:04d}.jpg", scene_id=image_id,
                            change_score=0.4, width=1366, height=768,
                            selection_reason="stable_frame")


def transcript(*spans):
    segs = [AudioSegment(id=i, start=s, end=e, text_raw=t, text_clean=t)
            for i, (s, e, t) in enumerate(spans, start=1)]
    return TranscriptionResult(language="ar", full_text_raw="", full_text_clean="",
                               segments=segs, words=[], engine={"name": "t"})


def test_no_text_lost_before_first_figure():
    t = transcript((0.0, 3.0, "مقدمة قبل أي لقطة."), (20.0, 24.0, "بعدها."))
    plan = TimelinePlanner().build(t, [kf(1, 10.0)], "عنوان")
    assert_lossless(plan)
    body = " ".join(b.text for s in plan.sections for b in s.blocks)
    assert "مقدمة قبل أي لقطة." in body


def test_every_segment_appears_exactly_once():
    t = transcript(*[(i * 5.0, i * 5.0 + 4.0, f"جملة رقم {i}.") for i in range(1, 21)])
    plan = TimelinePlanner().build(t, [kf(1, 12.0), kf(2, 55.0)], "عنوان")
    assert_lossless(plan)
    assert plan.total_segments_in == plan.total_segments_out == 20


def test_figures_are_never_dropped():
    t = transcript((0.0, 2.0, "كلام قصير."))
    keyframes = [kf(i, i * 30.0) for i in range(1, 6)]
    plan = TimelinePlanner().build(t, keyframes, "عنوان")
    assert_lossless(plan)
    assert plan.total_figures_out == 5


def test_segments_are_merged_into_readable_paragraphs():
    """مقاطع Whisper قصيرة؛ فقرة لكل مقطع تُنتج مستندًا مفتّتًا."""
    t = transcript(*[(i * 4.0, i * 4.0 + 3.5, f"جملة {i} من الشرح.")
                     for i in range(1, 13)])
    plan = TimelinePlanner().build(t, [], "عنوان")
    paragraphs = [b for s in plan.sections for b in s.blocks
                  if b.kind == "paragraph"]
    assert len(paragraphs) < 12, "لم تُدمج المقاطع في فقرات"
    assert all(len(p.text) > 20 for p in paragraphs)


def test_adaptive_sections_for_short_video():
    """فيديو 90 ثانية يجب ألا ينتهي بقسم واحد بلا بنية."""
    t = transcript(*[(i * 6.0, i * 6.0 + 5.0, f"جملة {i}.") for i in range(15)])
    plan = TimelinePlanner(adaptive_sections=True).build(t, [], "عنوان")
    assert len(plan.sections) >= 2, f"قسم واحد فقط: {len(plan.sections)}"


def test_adaptive_sections_for_long_lecture():
    """محاضرة ساعتين يجب ألا تنتج عشرات الأقسام."""
    t = transcript(*[(i * 30.0, i * 30.0 + 25.0, f"جملة {i}.") for i in range(240)])
    plan = TimelinePlanner(adaptive_sections=True).build(t, [], "عنوان")
    assert 3 <= len(plan.sections) <= 12, f"عدد أقسام غير عملي: {len(plan.sections)}"


def test_figure_precedes_text_at_same_time():
    t = transcript((10.0, 14.0, "شرح اللقطة."))
    plan = TimelinePlanner().build(t, [kf(1, 10.0)], "عنوان")
    kinds = [b.kind for s in plan.sections for b in s.blocks]
    assert kinds[0] == "figure"


def test_empty_transcript_still_produces_figures():
    plan = TimelinePlanner().build(transcript(), [kf(1, 5.0), kf(2, 9.0)], "عنوان")
    assert_lossless(plan)
    assert plan.total_figures_out == 2


def test_no_keyframes_still_keeps_all_text():
    t = transcript((0.0, 3.0, "أ."), (4.0, 7.0, "ب."))
    plan = TimelinePlanner().build(t, [], "عنوان")
    assert_lossless(plan)
    assert plan.total_segments_out == 2


def test_lossless_detects_dropped_segment():
    plan = DocumentPlan(title="t", total_segments_in=3, total_segments_out=2,
                        total_figures_in=0, total_figures_out=0)
    with pytest.raises(AssertionError, match="ضياع"):
        assert_lossless(plan)


def test_lossless_detects_duplicate_figure():
    plan = DocumentPlan(
        title="t",
        sections=[DocumentSection(title="s", blocks=[
            DocumentBlock(kind="figure", image_id=1),
            DocumentBlock(kind="figure", image_id=1)])],
        total_segments_in=0, total_segments_out=0,
        total_figures_in=1, total_figures_out=1)
    with pytest.raises(AssertionError, match="تكرار image_id"):
        assert_lossless(plan)


def test_sections_are_chronological():
    t = transcript(*[(i * 20.0, i * 20.0 + 15.0, f"جملة {i}.") for i in range(12)])
    plan = TimelinePlanner().build(t, [], "عنوان")
    starts = [s.start_timestamp for s in plan.sections]
    assert starts == sorted(starts)


def test_ocr_text_is_copied_from_keyframe_to_figure_block():
    """نص الشاشة (OCR) يُنسخ من KeyframeMetadata إلى كتلة الشكل — مصدر
    مختلف تمامًا عن caption المحسوبة من الكلام المصاحب."""
    t = transcript((0.0, 2.0, "كلام قصير."))
    keyframe = kf(1, 10.0)
    keyframe.ocr_text = "العنوان: مقدمة في الجبر الخطي"
    plan = TimelinePlanner().build(t, [keyframe], "عنوان")
    figures = [b for s in plan.sections for b in s.blocks if b.kind == "figure"]
    assert len(figures) == 1
    assert figures[0].ocr_text == "العنوان: مقدمة في الجبر الخطي"


def test_empty_ocr_text_stays_empty_in_figure_block():
    t = transcript((0.0, 2.0, "كلام قصير."))
    plan = TimelinePlanner().build(t, [kf(1, 10.0)], "عنوان")
    figures = [b for s in plan.sections for b in s.blocks if b.kind == "figure"]
    assert figures[0].ocr_text == ""
