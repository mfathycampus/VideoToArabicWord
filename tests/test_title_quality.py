"""العنوان المشتقّ من التفريغ — متى يُقبل ومتى يُردّ.

هذه الاختبارات مكتوبة على **نصوص خرجت فعلًا** من تسجيل حقيقي مدّته
8.5 دقائق، لا على أمثلة مصنوعة. ثلاثة من ستة عناوين كانت حلقات هلوسة،
ومع ذلك أعطى المقياس ‏``section_titling`` = 100٪.
"""
from __future__ import annotations

import pytest

from config.schemas import DocumentBlock, DocumentPlan, DocumentSection
from document.planner import _section_title
from document.quality import evaluate
from document.titles import (
    clean_title_candidate,
    is_acceptable_title,
    title_candidates,
    title_defects,
)

# ── نصوص حقيقية من مخرَج البرنامج على تسجيل المستخدم ──────────────────

LOOPS = [
    "أخطر القلب وأخطر القلب وأخطر القلب بعد أن أخطر القلب وأتفرر على القلب",
    "سنجد هذا الزجاج وصف الجدول سأقوم بتيدي وزع الجدول سأقوم بتيد جدول "
    "سأقوم بتيدي وزع الجدول",
]

USABLE = [
    "هنتعرف على كيف نخطط وكيف ننزل واجبات وكيف ننزل أخبار",
    "نقدر من Lesson، نختار حاجة اسمها Timetable، إما هيكون عندي الفصول",
    "الفصل الأول: مقدمة في الجبر الخطي",
    "قواعد اللغة العربية — الفعل والفاعل",
]


@pytest.mark.parametrize("text", LOOPS)
def test_hallucination_loops_are_rejected(text):
    assert not is_acceptable_title(text)
    assert title_defects(text)


@pytest.mark.parametrize("text", USABLE)
def test_usable_titles_are_accepted(text):
    assert is_acceptable_title(text), title_defects(text)


def test_truncation_cannot_hide_a_loop():
    """القصّ إلى سبعين حرفًا كان يبتر ذيل الحلقة فيُخفيها.

    هذه هي الثغرة بعينها التي مرّ منها العنوان الثالث: التكرار الثالث
    يقع بعد الحرف السبعين، فلو حُكم على المقصوص وحده لمرّ سليمًا.
    """
    long_loop = ("سنجد هذا الزجاج وصف الجدول سأقوم بتيدي وزع الجدول "
                 "سأقوم بتيد جدول سأقوم بتيدي وزع الجدول")
    assert len(long_loop) > 70
    assert clean_title_candidate(long_loop) == ""


def test_rhetorical_parallelism_is_not_a_loop():
    """«كيف… وكيف… وكيف…» عربيةٌ سليمة لا هلوسة.

    حدّ التكرار ثلاثةٌ لا اثنان لهذا السبب: بحدّ اثنين رُفض أدقّ وصف
    للمحاضرة في التسجيل كلّه.
    """
    assert is_acceptable_title(
        "هنتعرف على كيف نخطط وكيف ننزل واجبات وكيف ننزل أخبار")


def test_opening_noise_is_stripped_until_stable():
    """بسملة ثم تحية: تطبيقٌ واحد يترك الثانية عنوانًا.

    يبقى هذا التشذيب مستعمَلًا في **تعليقات الصور** المشتقّة من نصّ
    الشاشة، وإن لم تعد الرواية مصدرًا للعناوين.
    """
    text = ("بسم الله الرحمن الرحيم، السلام عليكم ورحمة الله وبركاته. "
            "هنتعرف على كيف نخطط وكيف ننزل واجبات وكيف ننزل أخبار.")
    first = next(iter(title_candidates(text)), "")
    assert first == "هنتعرف على كيف نخطط وكيف ننزل واجبات وكيف ننزل أخبار"


def test_narration_is_not_a_title_source():
    """العنوان لا يُنقل من أول الفقرة التي تليه.

    جُرّب فأنتج تلعثمًا: الجملة عنوانًا ثم الجملة نفسها نصًّا، ومضاعفةً
    لنصّ التفريغ في المستند. الترتيبي أصدق حتى تُصاغ عناوين حقيقية.
    """
    section = DocumentSection(
        title="", start_timestamp=0.0,
        blocks=[DocumentBlock(kind="paragraph",
                              text="هذه جملة افتتاحية صالحة تمامًا كعنوان.",
                              segment_ids=[1])])
    assert _section_title(1, section).startswith("القسم الأول —")


def test_screen_text_titles_can_be_disabled():
    """ملفّ شرح البرنامج يُطفئ نصّ الشاشة مصدرًا للعناوين."""
    section = DocumentSection(
        title="", start_timestamp=0.0,
        blocks=[DocumentBlock(kind="figure", text="", segment_ids=[],
                              ocr_text="مقدمة في الجبر الخطي")])
    assert _section_title(1, section, screen_text_titles=True) \
        == "مقدمة في الجبر الخطي"
    assert _section_title(1, section, screen_text_titles=False) \
        .startswith("القسم الأول —")


def test_browser_title_bar_never_becomes_a_section_title():
    """«Bi Lessons | Chalk - Googke Chrome» عنوانًا لقسم — حدث فعلًا."""
    section = DocumentSection(
        title="", start_timestamp=0.0,
        blocks=[DocumentBlock(kind="figure", text="", segment_ids=[],
                              ocr_text="Bi Lessons | Chalk - Googke Chrome")])
    assert _section_title(1, section, screen_text_titles=True) \
        .startswith("القسم الأول —")


def test_greeting_alone_yields_nothing():
    assert clean_title_candidate("السلام عليكم ورحمة الله وبركاته") == ""


def test_ordinal_title_is_a_defect():
    assert "ترتيبي" in title_defects("القسم الأول — من 00:00:00")


def test_too_long_is_a_defect():
    assert "طويل جدًّا" in title_defects("كلمة " * 40)


def test_short_human_titles_pass_judgement_but_not_extraction():
    """«خاتمة» عنوانٌ سليم يكتبه إنسان، وشظيّةٌ إن اقتُطع من كلام.

    الحكم والاشتقاق حدّان مختلفان لهذا السبب. خلطهما هو ما أرسب
    ``test_inapplicable_metrics_do_not_dilute_the_weights``: قسمٌ
    عنوانه «خاتمة» حصل على 87.5 بدل 100.
    """
    assert title_defects("خاتمة") == []
    assert clean_title_candidate("خاتمة") == ""


# ── المقياس نفسه ──────────────────────────────────────────────────────

def _plan(titles):
    sections = []
    for i, title in enumerate(titles):
        sections.append(DocumentSection(
            title=title,
            start_timestamp=float(i * 60),
            blocks=[DocumentBlock(kind="paragraph",
                                  text="نصّ القسم كافٍ للقياس. " * 4,
                                  segment_ids=[i])],
        ))
    return DocumentPlan(title="مستند", sections=sections)


def _titling(plan):
    report = evaluate(plan, [])
    return next(m for m in report.metrics if m.name == "section_titling")


def test_metric_fails_on_hallucinated_titles():
    """العطب الذي دفع إلى هذا التغيير: 100٪ لعناوين هلوسة.

    كانت البوابة تسأل «هل العنوان ترتيبي؟» فقط، فمرّت الحلقة نجاحًا.
    """
    assert _titling(_plan(LOOPS)).value == 0.0


def test_metric_accepts_real_titles():
    assert _titling(_plan(USABLE)).value == 100.0


def test_metric_reports_the_reason():
    detail = _titling(_plan(LOOPS)).detail
    assert "حلقة تكرار" in detail or "عبارة مكرّرة" in detail


def test_metric_mixed_document():
    metric = _titling(_plan(USABLE[:2] + LOOPS))
    assert metric.value == 50.0
