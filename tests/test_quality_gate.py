from config.schemas import DocumentBlock, DocumentPlan, DocumentSection, KeyframeMetadata
from document.quality import evaluate, assert_quality_gate


def test_quality_score_is_high_for_complete_plan():
    plan = DocumentPlan(
        title="t", total_segments_in=2, total_segments_out=2,
        total_figures_in=1, total_figures_out=1,
        sections=[DocumentSection(title="s", blocks=[
            DocumentBlock(kind="paragraph", text="a", segment_ids=[1], source_start=0, source_end=2),
            DocumentBlock(kind="paragraph", text="b", segment_ids=[2], source_start=2, source_end=4),
            DocumentBlock(kind="figure", image_id=1, image_filename="1.jpg", timestamp=3,
                          segment_ids=[], source_start=3, source_end=3),
        ])])
    report = evaluate(plan, [KeyframeMetadata(image_id=1, timestamp=3, filename="1.jpg", scene_id=1,
                                               change_score=.5, width=100, height=100,
                                               selection_reason="test")])
    assert report.overall >= 85
    assert_quality_gate(report)


def test_quality_detects_duplicate_references():
    plan = DocumentPlan(title="t", total_segments_in=1, total_figures_in=1,
                        sections=[DocumentSection(title="s", blocks=[
                            DocumentBlock(kind="paragraph", text="x", segment_ids=[1]),
                            DocumentBlock(kind="paragraph", text="y", segment_ids=[1]),
                            DocumentBlock(kind="figure", image_id=1),
                            DocumentBlock(kind="figure", image_id=1),
                        ])])
    report = evaluate(plan)
    assert report.duplicate_segments == 1
    assert report.duplicate_figures == 1
    try:
        assert_quality_gate(report)
    except AssertionError:
        pass
    else:
        raise AssertionError("duplicate references must fail the quality gate")
