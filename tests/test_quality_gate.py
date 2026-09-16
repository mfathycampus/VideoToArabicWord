"""بوابة الجودة — اختبارات تُثبت أن المقياس يستطيع الفشل.

النسخة السابقة كانت مثبَّتة رياضيًا: 95.0 أو 100.0 في كل تشغيلة من 27،
وعتبة الرسوب 85 لا تُبلَغ أبدًا، ومستندٌ بلا صورة واحدة يحصل على 100 —
أعلى من مستند فيه خمس صور. واختبارها كان
``test_quality_score_is_high_for_complete_plan``، وهو يمرّ على مقياس
معطوب تمامًا كما يمرّ على سليم.

كل اختبار هنا يبني خطة **سيئة بطريقة محدّدة** ويؤكّد أن الدرجة تنزل.
"""
from __future__ import annotations

import pytest

from config.schemas import DocumentBlock, DocumentPlan, DocumentSection
from document.quality import assert_quality_gate, evaluate

SENTENCE = "هذه جملة كاملة عن الموضوع المطروح في المحاضرة وتنتهي بنقطة. " * 4


def _para(text: str, ids: list[int] | None = None) -> DocumentBlock:
    return DocumentBlock(kind="paragraph", text=text,
                         segment_ids=ids or [], timestamp=0.0)


def _figure(image_id: int, caption: str = "", ocr: str = "") -> DocumentBlock:
    return DocumentBlock(kind="figure", image_id=image_id, caption=caption,
                         ocr_text=ocr, image_filename=f"{image_id}.jpg",
                         timestamp=float(image_id))


def _plan(sections: list[DocumentSection], **kwargs) -> DocumentPlan:
    return DocumentPlan(title="محاضرة", sections=sections, **kwargs)


def _good_section(title: str = "مقدّمة في الخوارزميات") -> DocumentSection:
    return DocumentSection(title=title, blocks=[
        _para(SENTENCE, [1]), _para(SENTENCE, [2])])


# ── القاعدة الأولى: كل مقياس يستطيع الفشل ────────────────────────────

def test_broken_paragraphs_lower_the_score():
    """الفقرة المقطوعة في منتصف الجملة أوضح عيب في المخرج الحالي.

    كل صورة تقطع الفقرة الجارية أيًّا كان موضعها من الجملة — قياس على
    محاضرة ساعة أعطى 86 فقرة مقطوعة.
    """
    whole = _plan([DocumentSection(title="فصل الخوارزميات", blocks=[
        _para(SENTENCE, [1]), _para(SENTENCE, [2])])])
    cut = _plan([DocumentSection(title="فصل الخوارزميات", blocks=[
        _para(SENTENCE.rstrip(". ") + " ثم", [1]),
        _para(SENTENCE.rstrip(". ") + " وبعد", [2])])])

    assert evaluate(whole).get("paragraph_flow") == 100.0
    assert evaluate(cut).get("paragraph_flow") == 0.0
    assert evaluate(cut).overall < evaluate(whole).overall


def test_a_wall_of_text_lowers_the_score():
    """فقرة واحدة طولها آلاف الحروف: حالة حقيقية بلا صور تقطع النص."""
    wall = _plan([DocumentSection(title="فصل الخوارزميات",
                                  blocks=[_para(SENTENCE * 40, [1])])])
    assert evaluate(wall).get("paragraph_balance") == 0.0


def test_ordinal_section_titles_lower_the_score():
    """«القسم الأول — من 00:00:00» يملأ خانة العنوان ولا يفيد قارئًا.

    كل مستند يُنتجه البرنامج اليوم عناوينه كلها من هذا الشكل، فالفهرس
    المبني عليه بلا وظيفة — وهذا ما يجب أن يظهر في الدرجة.
    """
    ordinal = _plan([
        DocumentSection(title="القسم الأول — من 00:00:00",
                        blocks=[_para(SENTENCE, [1])]),
        DocumentSection(title="القسم الثاني — من 00:05:00",
                        blocks=[_para(SENTENCE, [2])]),
    ])
    named = _plan([
        DocumentSection(title="تعريف الخوارزمية",
                        blocks=[_para(SENTENCE, [1])]),
        DocumentSection(title="أمثلة تطبيقية",
                        blocks=[_para(SENTENCE, [2])]),
    ])

    assert evaluate(ordinal).get("section_titling") == 0.0
    assert evaluate(named).get("section_titling") == 100.0


@pytest.mark.parametrize("caption", [
    "لقطة عند 00:00:03",     # ما يولّده planner.py:158 فعلًا
    "صورة 1",
    "صورة 2 — 00:00:20",
    "شكل 5",
])
def test_the_auto_caption_the_planner_actually_writes_counts_as_generic(caption):
    """حارس ضدّ إنذار كاذب رأيناه فعلًا.

    النمط الأول كان يطابق «صورة N» وحدها — صيغة لا يولّدها البرنامج
    إطلاقًا — فأعطى المقياس **100٪ كاذبة** على كل مستند في مصفوفة
    القبول. مقياس لا يطابق المخرج الحقيقي لا يقيس شيئًا.
    """
    plan = _plan([DocumentSection(title="الأشكال", blocks=[
        _para(SENTENCE, [1]), _figure(1, caption=caption)])])
    assert evaluate(plan).get("figure_captioning") == 0.0


def test_generic_figure_captions_lower_the_score():
    """التعليق التلقائي لا يصف الصورة."""
    generic = _plan([DocumentSection(title="الأشكال", blocks=[
        _para(SENTENCE, [1]),
        _figure(1, caption="لقطة عند 00:00:10"),
        _figure(2, caption="لقطة عند 00:00:20"),
    ])])
    described = _plan([DocumentSection(title="الأشكال", blocks=[
        _para(SENTENCE, [1]),
        _figure(1, caption="مخطّط تدفّق الخوارزمية"),
        _figure(2, caption="جدول تعقيد العمليات",
                ocr="جدول تعقيد العمليات"),
    ])])

    assert evaluate(generic).get("figure_captioning") == 0.0
    assert evaluate(described).get("figure_captioning") == 100.0


def test_screen_text_alone_is_not_a_caption():
    """وجود نصّ شاشة ليس تعليقًا — وهذا العيب بعينه.

    الشرط السابق كان ``b.ocr_text.strip()``، فمنح 100٪ لمستند حقيقي
    تعليقاته «لقطة عند 00:01:34» بينما نصّ شاشته «ston. © Freee (ease
    text ner». المقياس كان يقيس تشغيل Tesseract لا جودة التعليق.
    """
    plan = _plan([DocumentSection(title="الأشكال", blocks=[
        _para(SENTENCE, [1]),
        _figure(1, caption="لقطة عند 00:00:10",
                ocr="ston. © Freee (ease text ner"),
    ])])
    assert evaluate(plan).get("figure_captioning") == 0.0


def test_near_duplicate_slides_are_detected_by_their_screen_text():
    """شريحة تُبنى تدريجيًا تنجو من فحص التكرار البصري."""
    plan = _plan([DocumentSection(title="الشرائح", blocks=[
        _para(SENTENCE, [1]),
        _figure(1, ocr="العنوان: المقدّمة"),
        _figure(2, ocr="العنوان: المقدّمة — نقطة أولى"),
        _figure(3, ocr="خاتمة مختلفة تمامًا"),
    ])])
    assert evaluate(plan).get("figure_distinctness") < 100.0


def test_unbalanced_sections_lower_the_score():
    """قسم يبتلع نصف المحاضرة وآخر من سطر: الحدود وقعت على الساعة."""
    even = _plan([_good_section("أ"), _good_section("ب")])
    skewed = _plan([
        DocumentSection(title="أ", blocks=[_para(SENTENCE * 30, [1])]),
        DocumentSection(title="ب", blocks=[_para(SENTENCE, [2])]),
    ])
    assert evaluate(skewed).get("section_balance") < \
        evaluate(even).get("section_balance")


# ── القاعدة الثانية: غير المنطبق يُستبعَد لا يُمنَح 100 ──────────────

def test_a_document_with_no_figures_is_not_rewarded_for_it():
    """العطل الأصلي بعينه.

    ``_ratio(0, 0)`` كان يُعيد 100، فمستندٌ أنتج **صفر صور** حصل على
    100.0 — أعلى من المرجع الذي أنتج خمس صور (95.0). المقياس كان يكافئ
    غياب المحتوى.
    """
    textless = _plan([_good_section()])
    report = evaluate(textless)

    assert report.get("figure_captioning") is None
    assert report.get("figure_distinctness") is None

    with_figures = _plan([DocumentSection(title="مقدّمة في الخوارزميات", blocks=[
        _para(SENTENCE, [1]), _para(SENTENCE, [2]),
        _figure(1, caption="مخطّط تدفّق"), _figure(2, caption="جدول التعقيد"),
    ])])
    assert evaluate(with_figures).overall >= report.overall, (
        "مستند بصور موصوفة لا يجوز أن يخسر أمام مستند بلا صور")


def test_score_is_none_when_there_is_nothing_to_measure():
    assert evaluate(_plan([])).overall is None


def test_inapplicable_metrics_do_not_dilute_the_weights():
    """الأوزان تُعاد تطبيعها على المنطبق وحده.

    لولا ذلك لكان مستند نصّي خالص محكومًا بسقف أدنى من 100 لأن وزن
    الصور مفقود — عقابٌ على شيء لا وجود له.
    """
    report = evaluate(_plan([_good_section(), _good_section("خاتمة")]))
    assert report.overall == pytest.approx(100.0)


# ── الخلل البنيوي: فحص لا درجة ───────────────────────────────────────

def test_duplicate_references_fail_the_gate():
    plan = _plan([DocumentSection(title="أ", blocks=[
        _para(SENTENCE, [1]), _para(SENTENCE, [1]),
        _figure(1), _figure(1)])])
    report = evaluate(plan)

    assert report.diagnostics["duplicate_segments"] == 1
    assert report.diagnostics["duplicate_figures"] == 1
    with pytest.raises(AssertionError) as excinfo:
        assert_quality_gate(report)
    assert "بنيوي" in str(excinfo.value)


def test_missing_segments_fail_the_gate():
    """ضمانة ADR-010 صارت فحصًا بنيويًا بدل أن تكون درجةً مثبَّتة."""
    plan = _plan([DocumentSection(title="أ", blocks=[_para(SENTENCE, [1])])],
                 total_segments_in=5)
    report = evaluate(plan)

    assert any("ADR-010" in v for v in report.violations)
    with pytest.raises(AssertionError):
        assert_quality_gate(report)


def test_a_low_score_does_not_fail_the_gate_by_default():
    """قرار مقصود: الدرجة تُتابَع ولا تُفرَض بعد.

    المخرج الحالي ضعيف في هذه المقاييس بحكم تصميمه. فرض عتبة اليوم
    يُسقط كل مهمة على جهاز كل معلّم، عقابًا على عيب لم يُصلَح بعد.
    """
    bad = _plan([DocumentSection(title="القسم الأول — من 00:00:00",
                                 blocks=[_para("مقطوع ثم", [1])])])
    report = evaluate(bad)

    assert report.overall < 50.0
    assert_quality_gate(report)                     # لا استثناء

    with pytest.raises(AssertionError):
        assert_quality_gate(report, minimum=85.0)   # حين تُطلَب صراحةً


def test_report_serialises_with_inapplicable_metrics():
    """‏``quality.json`` يجب أن يبقى قابلًا للتسلسل مع قيم ``None``."""
    import json

    data = evaluate(_plan([_good_section()])).as_dict()
    json.dumps(data, ensure_ascii=False)

    assert data["metrics"]["figure_captioning"]["value"] is None
    assert "filler_per_1000_words" in data["diagnostics"]


def test_filler_is_reported_but_never_scored():
    """‏ADR-010 يمنع حذف الحشو، فقياسه تشخيص لا حكم."""
    plan = _plan([DocumentSection(title="مقدّمة في الخوارزميات", blocks=[
        _para("يعني " + SENTENCE + " طيب يعني كده.", [1])])])
    report = evaluate(plan)

    assert report.diagnostics["filler_per_1000_words"] > 0
    assert all(m.name != "filler" for m in report.metrics)


def test_paragraph_balance_detail_names_short_fragments():
    """0٪ بسبب الشظايا يجب أن يقول «أقصر» لا «أطول فقرة 139 (الحدّ 1400)»."""
    from document.quality import _paragraph_balance

    class Block:
        def __init__(self, text):
            self.kind = "paragraph"
            self.text = text
            self.bullets = []

    metric = _paragraph_balance([Block("جملة قصيرة."), Block("أخرى.")])
    assert metric.value == 0.0
    assert "2 فقرة أقصر" in metric.detail
    assert "0 أطول" in metric.detail


# ── الاكتمال ─────────────────────────────────────────────────────────
class _Seg:
    def __init__(self, start, end, text):
        self.start, self.end = start, end
        self.text_raw = self.text_clean = text


def test_sparse_transcript_is_flagged_even_when_paragraphs_read_well():
    """تسجيل 5:12 حقيقي: 24٪ مفرَّغة، وفجوة 89 ثانية، ودرجة قراءة 75٪."""
    from document.quality import completeness

    segments = [_Seg(0.5, 13.2, "السلام عليكم " * 4),
                _Seg(173.7, 193.4, "كلمة " * 20),
                _Seg(282.3, 312.5, "كلمة " * 15)]
    info, warnings = completeness(segments, 312.6)

    assert info["transcript_coverage_pct"] < 40
    assert info["longest_gap_seconds"] == pytest.approx(160.5, abs=0.1)
    assert [193.4, 282.3] in info["long_gaps"]
    assert any(w.startswith("transcript_coverage") for w in warnings)
    assert any(w.startswith("words_per_minute") for w in warnings)
    assert any("3:13 ← 4:42" in w for w in warnings)


def test_dense_transcript_raises_no_completeness_warning():
    from document.quality import completeness

    segments = [_Seg(t, t + 9.5, "كلمة " * 20) for t in range(0, 300, 10)]
    info, warnings = completeness(segments, 300.0)

    assert info["transcript_coverage_pct"] >= 90
    assert warnings == []


def test_clip_offset_does_not_invent_a_leading_gap():
    from document.quality import completeness

    segments = [_Seg(t, t + 9.5, "كلمة " * 20) for t in range(300, 600, 10)]
    info, warnings = completeness(segments, 300.0, start_offset=300.0)
    assert info["long_gaps"] == []


def test_evaluate_without_segments_keeps_old_behaviour():
    from document.quality import evaluate

    plan = _plan([DocumentSection(title="فصل الخوارزميات",
                                  blocks=[_para(SENTENCE * 3, [1])])])
    assert "transcript_coverage_pct" not in evaluate(plan).diagnostics
