"""إعادة الصياغة من طرف إلى طرف — عبر خادم Claude وهمي."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from ai.providers import AnthropicProvider
from ai.rewriter import RewriteConfig, TranscriptRewriter, _extract_json
from config.schemas import AudioSegment, KeyframeMetadata, TranscriptionResult
from document.planner import assert_lossless

CALLS = {"n": 0}


class Handler(BaseHTTPRequestHandler):
    mode = "ok"

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length))
        CALLS["n"] += 1
        is_outline = "أقسام الوثيقة" in body["messages"][0]["content"]
        if Handler.mode == "broken":
            payload = "هذا ليس JSON إطلاقًا"
        elif is_outline:
            payload = json.dumps({"title": "دليل تسجيل الطلاب",
                                  "abstract": "ملخّص تنفيذي للوثيقة.",
                                  "key_points": ["نقطة أولى", "نقطة ثانية"]},
                                 ensure_ascii=False)
        else:
            payload = "```json\n" + json.dumps(
                {"title": "قسم مُعاد صياغته", "summary": "ملخّص القسم.",
                 "paragraphs": ["فقرة أولى محرّرة.", "فقرة ثانية محرّرة."]},
                ensure_ascii=False) + "\n```"
        data = json.dumps({"content": [{"type": "text", "text": payload}]})
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(data.encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    CALLS["n"] = 0
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def make_transcript(count=24):
    segments = [AudioSegment(id=i, start=i * 8.0, end=i * 8.0 + 7.0,
                             text_raw=f"يعني الجملة رقم {i} من الشرح آه",
                             text_clean=f"يعني الجملة رقم {i} من الشرح آه")
                for i in range(1, count + 1)]
    return TranscriptionResult(language="ar", full_text_raw="",
                               full_text_clean="", segments=segments,
                               words=[], engine={"name": "t"})


def keyframes(count=4):
    return [KeyframeMetadata(image_id=i, timestamp=i * 40.0,
                             filename=f"{i:04d}.jpg", scene_id=i,
                             change_score=0.4, width=1366, height=768,
                             selection_reason="stable_frame")
            for i in range(1, count + 1)]


def rewriter(server, monkeypatch, batch_chars=400):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    provider = AnthropicProvider(base_url=server)
    return TranscriptRewriter(
        provider, RewriteConfig(enabled=True, provider="anthropic",
                                batch_chars=batch_chars))


def test_rewrite_produces_structured_plan(server, monkeypatch):
    plan = rewriter(server, monkeypatch).build_plan(
        make_transcript(), keyframes(), "احتياطي")
    assert plan.title == "دليل تسجيل الطلاب"
    assert plan.abstract
    assert len(plan.key_points) == 2
    assert plan.generated_by.startswith("ai:anthropic")
    assert len(plan.sections) >= 2


def test_rewrite_never_loses_text_or_figures(server, monkeypatch):
    """ADR-010 يبقى مفروضًا حتى بعد إعادة الصياغة."""
    transcript = make_transcript(24)
    plan = rewriter(server, monkeypatch).build_plan(
        transcript, keyframes(5), "احتياطي")
    assert_lossless(plan)
    assert plan.total_segments_in == plan.total_segments_out == 24
    assert plan.total_figures_in == plan.total_figures_out == 5


def test_every_figure_appears_exactly_once(server, monkeypatch):
    plan = rewriter(server, monkeypatch).build_plan(
        make_transcript(), keyframes(6), "احتياطي")
    ids = [b.image_id for s in plan.sections for b in s.blocks
           if b.kind == "figure"]
    assert sorted(ids) == list(range(1, 7))


def test_broken_model_output_refuses_to_pose_as_a_rewrite(server, monkeypatch):
    """ردٌّ غير صالح في **كل** الدفعات يرفع استثناءً بسببه.

    كان هذا الاختبار يؤكّد العكس — أن الخطة تخرج موسومة ``ai:`` ومحتواها
    خام — وهو ما ثبت أنه عطل لا ميزة: خرج مستندٌ حقيقي بغلافٍ مكتوب
    عليه «صياغة النص: ai:anthropic/claude-sonnet-5» ونصُّه خامٌ كلّه.

    وضمانة «لا يُسقط المحتوى» باقية، لكن موضعها الصحيح هو الـ pipeline:
    يلتقط الاستثناء ويبني الخطة بالمُخطِّط الزمنيّ — بلا ادّعاء.
    """
    from ai.providers import RewriteUnavailableError
    from document.planner import TimelinePlanner

    Handler.mode = "broken"
    try:
        transcript = make_transcript(12)
        with pytest.raises(RewriteUnavailableError) as caught:
            rewriter(server, monkeypatch).build_plan(transcript, [], "احتياطي")
        assert "فشلت كل دفعات" in str(caught.value)

        # ومسار السقوط يحفظ النصّ كاملًا — وهو ما كان يُفحص هنا أصلًا
        plan = TimelinePlanner().build(transcript, [], "احتياطي")
        assert_lossless(plan)
        body = " ".join(b.text for s in plan.sections for b in s.blocks)
        assert "الجملة رقم 1" in body, "ضاع النص عند فشل الصياغة"
        assert plan.generated_by == "timeline"
    finally:
        Handler.mode = "ok"


def test_traceability_segment_ids_are_recorded(server, monkeypatch):
    """كل قسم يحمل معرّفات مقاطعه — التتبّع للنص الخام ممكن دائمًا."""
    plan = rewriter(server, monkeypatch).build_plan(
        make_transcript(16), [], "احتياطي")
    recorded = [i for s in plan.sections for b in s.blocks
                for i in b.segment_ids]
    assert sorted(recorded) == list(range(1, 17))


def test_batching_reduces_call_count(server, monkeypatch):
    """دفعات كبيرة = طلبات أقل = كلفة أقل."""
    rewriter(server, monkeypatch, batch_chars=200).build_plan(
        make_transcript(20), [], "احتياطي")
    many = CALLS["n"]
    CALLS["n"] = 0
    rewriter(server, monkeypatch, batch_chars=5000).build_plan(
        make_transcript(20), [], "احتياطي")
    assert CALLS["n"] < many


def test_empty_transcript_is_rejected_clearly(server, monkeypatch):
    from ai.providers import RewriteUnavailableError
    empty = TranscriptionResult(language="ar", full_text_raw="",
                                full_text_clean="", segments=[], words=[],
                                engine={"name": "t"})
    with pytest.raises(RewriteUnavailableError):
        rewriter(server, monkeypatch).build_plan(empty, [], "احتياطي")


@pytest.mark.parametrize("raw,expected", [
    ('{"a":1}', {"a": 1}),
    ('```json\n{"a":1}\n```', {"a": 1}),
    ('نص قبل {"a":1} نص بعد', {"a": 1}),
    ("لا يوجد", None),
    ("", None),
])
def test_json_extraction_tolerates_model_chatter(raw, expected):
    assert _extract_json(raw) == expected


def test_figures_are_interleaved_with_their_explanatory_text(server,
                                                             monkeypatch):
    """عيب مُبلَّغ: كل الصور تتكدّس ثم يأتي الشرح كله في آخر القسم.

    الصورة يجب أن تجاور النص الذي يشرحها زمنيًا، لا أن تُرمى قبله.
    """
    transcript = make_transcript(40)          # يمتد حتى 00:05:27
    plan = rewriter(server, monkeypatch).build_plan(
        transcript, keyframes(6), "احتياطي")

    kinds = [b.kind for s in plan.sections for b in s.blocks]
    assert kinds.count("figure") == 6
    # لا تسلسل من ثلاث صور متتالية دون نص بينها
    runs = 0
    longest = 0
    for kind in kinds:
        runs = runs + 1 if kind == "figure" else 0
        longest = max(longest, runs)
    assert longest < 3, f"صور متتالية بلا شرح: {kinds}"

    # وداخل كل قسم: ترتيب زمني صاعد للكتل جميعًا
    for section in plan.sections:
        stamps = [b.timestamp for b in section.blocks
                  if b.timestamp is not None]
        assert stamps == sorted(stamps), "ترتيب الكتل غير زمني"


def test_short_video_still_gets_several_sections(server, monkeypatch):
    """عيب مُبلَّغ: «عدد الأقسام: 1» لفيديو كامل — بلا بنية ولا فهرس."""
    short = rewriter(server, monkeypatch, batch_chars=3500).build_plan(
        make_transcript(30), keyframes(3), "احتياطي")
    assert len(short.sections) >= 2, f"أقسام قليلة: {len(short.sections)}"

    long_plan = rewriter(server, monkeypatch, batch_chars=3500).build_plan(
        make_transcript(180), keyframes(8), "احتياطي")
    assert len(long_plan.sections) >= 4, (
        f"أقسام قليلة: {len(long_plan.sections)}")


def test_explicit_batch_chars_stays_an_upper_bound(server, monkeypatch):
    """الاشتقاق التلقائي يخفض حجم الدفعة ولا يتجاوز سقف المستخدم."""
    from ai.rewriter import TranscriptRewriter as TR
    segments = make_transcript(200).segments
    small = rewriter(server, monkeypatch, batch_chars=250)
    assert small._effective_batch_chars(segments) == 250
    large = rewriter(server, monkeypatch, batch_chars=100000)
    limit = large._effective_batch_chars(segments)
    assert TR.MIN_BATCH_CHARS <= limit < 100000


def test_generated_by_names_the_actual_model(server, monkeypatch):
    """عيب مُبلَّغ: «ai:anthropic/default» بدل اسم النموذج الحقيقي."""
    plan = rewriter(server, monkeypatch).build_plan(
        make_transcript(12), [], "احتياطي")
    assert plan.generated_by.startswith("ai:anthropic/claude")
    assert "default" not in plan.generated_by


def test_each_figure_carries_a_descriptive_caption(server, monkeypatch):
    """عيب مُبلَّغ: الصورة تخرج بلا أي شرح.

    الخادم الوهمي لا يعيد figures، فالتسمية تبقى فارغة — لكن مسار
    الاستخراج نفسه يجب أن يعمل حين يعيدها النموذج.
    """
    from ai.rewriter import TranscriptRewriter as TR
    rw = rewriter(server, monkeypatch)
    batch = make_transcript(4).segments
    out = rw._rewrite_batch(batch, keyframes(2))
    assert "captions" in out


def test_model_captions_reach_the_figure_blocks(server, monkeypatch):
    """تعليقات النموذج تُطبَّع زمنيًا وتُركَّب على الصور الصحيحة."""
    from ai.rewriter import TranscriptRewriter as TR
    entry = {"title": "ق", "summary": "", "paragraphs": ["نص."],
             "segment_ids": [1], "start": 0.0, "end": 100.0,
             "spans": [(0.0, 100.0, 4)],
             "captions": {"00:00:40": "قائمة الفصول الدراسية"}}
    sections = TR._assemble([entry], keyframes(1))
    figure = [b for b in sections[0].blocks if b.kind == "figure"][0]
    assert figure.caption == "قائمة الفصول الدراسية"


def test_paragraph_times_spread_inside_a_single_segment(server, monkeypatch):
    """دفعة من مقطع واحد كانت تمنح كل فقراتها نفس الزمن."""
    from ai.rewriter import TranscriptRewriter as TR
    entry = {"paragraphs": ["أ" * 30, "ب" * 30, "ج" * 30],
             "start": 10.0, "spans": [(10.0, 40.0, 90)]}
    times = TR._paragraph_times(entry)
    assert times == sorted(times)
    assert len(set(times)) == 3, f"أزمنة متطابقة: {times}"
    assert 10.0 <= times[0] < times[-1] < 40.0


# ── السياق الذي يحسم الأخطاء السمعية ───────────────────────────────────
def _recording_rewriter(monkeypatch, **config):
    from ai.rewriter import TranscriptRewriter as TR

    prompts = []

    def fake_complete(self, system_prompt, user_prompt, max_tokens=2048):
        prompts.append((system_prompt, user_prompt))
        return json.dumps({"title": "متابعة تحضير المعلمات", "summary": "م.",
                           "paragraphs": ["فقرة."]}, ensure_ascii=False)

    monkeypatch.setattr(TR, "_complete_with_retry", fake_complete)
    rw = TR(provider=type("P", (), {"info": type("I", (), {"name": "fake"})(),
                                    "model": "m"})(),
            config=RewriteConfig(enabled=True, **config))
    return rw, prompts


def test_model_sees_title_screen_text_and_glossary(monkeypatch):
    """رُصد حقيقةً: «المدارس» ← «المتاجر» لأن النموذج لم يرَ «High School»."""
    rw, prompts = _recording_rewriter(monkeypatch, glossary="Lesson Planner")
    frames = keyframes(1)
    frames[0].ocr_text = "MRNS Elementary School\nLesson Feedback"

    rw.build_plan(make_transcript(3), frames, "متابعة تحضير المعلمين")

    system, user = prompts[0]
    assert "خطأ سمعي" in system and "[غير واضح]" in system
    assert "عنوان التسجيل: متابعة تحضير المعلمين" in user
    assert "MRNS Elementary School" in user
    assert "Lesson Planner" in user
    # السياق يسبق التفريغ، والتفريغ ما زال كاملًا
    assert user.index("السياق") < user.index("التفريغ الخام")
    # والملخّص التنفيذي يرى العنوان أيضًا
    assert "عنوان التسجيل" in prompts[-1][1]


def test_screen_text_can_be_withheld(monkeypatch):
    """نصّ الشاشة قد يحمل أسماء أشخاص — والإعداد يمنع إرساله."""
    rw, prompts = _recording_rewriter(monkeypatch, include_screen_text=False)
    frames = keyframes(1)
    frames[0].ocr_text = "Abdullah Omran"

    rw.build_plan(make_transcript(3), frames, "درس")

    assert all("Abdullah" not in user for _, user in prompts)


def test_screen_context_is_capped(monkeypatch):
    from ai.rewriter import TranscriptRewriter as TR

    rw, prompts = _recording_rewriter(monkeypatch)
    frames = keyframes(1)
    frames[0].ocr_text = "\n".join(f"Menu item number {i}" for i in range(500))

    rw.build_plan(make_transcript(3), frames, "درس")

    screen_line = [line for line in prompts[0][1].splitlines() if "نصوص ظاهرة" in line][0]
    assert len(screen_line) < TR.SCREEN_CONTEXT_CHARS + 100


def test_empty_paragraphs_get_one_retry(monkeypatch):
    """رُصد: أوّل دفعة خرجت خامًا لأن النموذج أعاد paragraphs فارغة."""
    from ai.rewriter import TranscriptRewriter as TR

    replies = [json.dumps({"title": "", "paragraphs": []}),
               json.dumps({"title": "الدخول إلى النظام", "summary": "م.",
                           "paragraphs": ["يدخل المستخدم إلى لوحة الإدارة."]},
                          ensure_ascii=False)]
    prompts = []

    def fake_complete(self, system_prompt, user_prompt, max_tokens=2048):
        prompts.append(user_prompt)
        return replies[len(prompts) - 1]

    monkeypatch.setattr(TR, "_complete_with_retry", fake_complete)
    rw = TR(provider=None, config=RewriteConfig(enabled=True))
    out = rw._rewrite_batch(make_transcript(3).segments, [])

    assert len(prompts) == 2 and "لا تُعِد paragraphs فارغة" in prompts[1]
    assert out["failed"] is False
    assert out["paragraphs"] == ["يدخل المستخدم إلى لوحة الإدارة."]


# ── دمج الفقرات القصيرة ────────────────────────────────────────────────
def _p(text, ids=()):
    from config.schemas import DocumentBlock
    return DocumentBlock(kind="paragraph", text=text, segment_ids=list(ids))


def _f(image_id):
    from config.schemas import DocumentBlock
    return DocumentBlock(kind="figure", image_id=image_id,
                         image_filename=f"{image_id}.jpg")


def test_short_neighbour_paragraphs_are_merged():
    from ai.rewriter import merge_short_paragraphs

    out = merge_short_paragraphs([_p("جملة أولى.", [1]), _p("جملة ثانية.", [2])])
    assert [b.text for b in out] == ["جملة أولى. جملة ثانية."]
    assert out[0].segment_ids == [1, 2]


def test_short_paragraph_split_by_a_figure_joins_the_next_one():
    from ai.rewriter import merge_short_paragraphs

    out = merge_short_paragraphs([_p("قصيرة."), _f(1), _p("تكملة.")])
    assert [b.kind for b in out] == ["paragraph", "figure"]
    assert out[0].text == "قصيرة. تكملة."


def test_long_paragraphs_and_the_cap_are_respected():
    from ai.rewriter import MERGED_PARAGRAPH_MAX_CHARS, merge_short_paragraphs

    long_a, long_b = "أ" * 400, "ب" * 400
    assert len(merge_short_paragraphs([_p(long_a), _p(long_b)])) == 2
    near_cap = "ج" * (MERGED_PARAGRAPH_MAX_CHARS - 5)
    assert len(merge_short_paragraphs([_p(near_cap), _p("قصيرة جدًا")])) == 2


def test_anonymize_asks_the_model_for_roles_not_names(monkeypatch):
    rw, prompts = _recording_rewriter(monkeypatch, anonymize_names=True)
    rw.build_plan(make_transcript(3), [], "درس")
    assert "لا تكتب اسم أي شخص" in prompts[0][1]

    rw, prompts = _recording_rewriter(monkeypatch)
    rw.build_plan(make_transcript(3), [], "درس")
    assert "لا تكتب اسم أي شخص" not in prompts[0][1]
