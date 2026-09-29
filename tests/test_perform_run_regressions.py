"""انحداراتٌ من التشغيل الحقيقي الأول للوضع المرئي (1.14.0، 28 سبتمبر 2026).

«تدريب بيرفورم 2026-2027 — الأقسام الوطنية»: اجتماع Teams مدّته 81
دقيقة بمشاركة شاشة، 128 لقطة، الصياغة عبر Claude. ما خرج:

* الوضع المرئي «ليس JSON بالشكل المطلوب» — سقط كلّه.
* 14 من 16 دفعة نصّية «بلا فقرات» — نصٌّ عامّيّ خام بفقرات 1400+ حرف.
* 14 عنوانًا «المقطع الزمني 00:05:10»، والمقياس أعطاها 100٪.
* الحزمة التعليمية «anthropic غير متاح» مع أن الصياغة نجحت عبره.
* لوحة المشاركين (وجوه وأسماء) في كل لقطة، وأسماء مقصوصة في التعليقات.
* مسرد فيه «pervisor» و«Start» ورأس جدول من ست كلمات.

كل اختبار هنا يثبّت علاج واحدٍ منها.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from ai import providers
from ai.providers import (
    AnthropicProvider,
    ProviderInfo,
    ResponseTruncatedError,
    RewriteUnavailableError,
    dump_failed_response,
    set_response_dump_dir,
)
from ai.rewriter import (
    RewriteConfig,
    TranscriptRewriter,
    _extract_json,
    raw_paragraphs,
)
from ai.visual_author import VisualAuthor, VisualAuthorConfig
from config.schemas import AudioSegment, KeyframeMetadata, TranscriptionResult
from document.planner import _figure_caption, assert_lossless
from document.titles import title_defects


# ── أدوات ───────────────────────────────────────────────────────────

def _segments(count: int, chars: int = 60, step: float = 8.0):
    text = ("شرح خطوات النظام " * 20)[:chars]
    return [AudioSegment(id=i, start=i * step, end=i * step + step - 1,
                         text_raw=text, text_clean=text)
            for i in range(1, count + 1)]


def _transcript(segments):
    return TranscriptionResult(language="ar", full_text_raw="", full_text_clean="",
                               segments=segments, words=[], engine={"name": "t"})


def _frames(tmp_path: Path, count: int, step: float = 10.0):
    frames = []
    for i in range(1, count + 1):
        name = f"{i:04d}.jpg"
        Image.new("RGB", (320, 180), (20 * i % 255, 120, 200)).save(tmp_path / name)
        frames.append(KeyframeMetadata(
            image_id=i, timestamp=step * i, filename=name, scene_id=i,
            change_score=0.3, width=320, height=180,
            selection_reason="stable_frame"))
    return frames


class FakeVision:
    info = ProviderInfo(name="fake", label_ar="مزيّف", is_local=True, privacy_note="")
    supports_vision = True
    model = "fake"

    def __init__(self, respond):
        self.respond = respond
        self.calls = []

    def is_available(self):
        return True

    def complete(self, system_prompt, user_prompt, max_tokens=2048, timeout=180):
        self.calls.append((user_prompt, max_tokens))
        return self.respond(user_prompt, max_tokens)


def _sent_ids(parts) -> list[int]:
    ids = []
    for part in parts if isinstance(parts, list) else []:
        text = part.get("text", "")
        if text.startswith("[لقطة "):
            ids.append(int(text.split()[1]))
    return ids


# ── ١. القطع عند سقف الرموز يُكشف صراحةً ─────────────────────────────

def test_anthropic_max_tokens_stop_is_raised_with_the_partial_text():
    data = {"content": [{"type": "text", "text": '{"sections": [{"title": "نصف'}],
            "stop_reason": "max_tokens", "usage": {"output_tokens": 2048}}
    with pytest.raises(ResponseTruncatedError) as caught:
        AnthropicProvider._extract_text(data)
    assert caught.value.partial.startswith('{"sections"')
    assert isinstance(caught.value, RewriteUnavailableError)
    ok = {"content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn"}
    assert AnthropicProvider._extract_text(ok) == "{}"


def test_extract_json_survives_preamble_and_trailing_braces():
    raw = ('إليك المستند:\n```json\n{"title": "أ", "paragraphs": ["ب"]}\n```\n'
           "ملاحظة: الحقل {figures} فارغ.")
    assert _extract_json(raw) == {"title": "أ", "paragraphs": ["ب"]}
    raw = 'قبل {"title": "أ"} وبعد {غير صالح}'
    assert _extract_json(raw) == {"title": "أ"}


# ── ٢. الصياغة النصّية تتعافى من القطع ومن الردّ غير الصالح ───────────

class _TruncatingClaude(BaseHTTPRequestHandler):
    """خادم Claude وهمي يقطع الردّ ما لم يكن السقف 16000 فأكثر."""
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        _TruncatingClaude.seen.append(body["max_tokens"])
        outline = "أقسام الوثيقة" in body["messages"][0]["content"]
        if outline:
            text, stop = json.dumps({"title": "دليل", "abstract": "", "key_points": []},
                                    ensure_ascii=False), "end_turn"
        elif body["max_tokens"] < 16000:
            text, stop = '{"title": "قسم", "paragraphs": ["فقرة مقطو', "max_tokens"
        else:
            text, stop = json.dumps({"title": "إعداد الاستمارات", "summary": "",
                                     "paragraphs": ["فقرة محرّرة كاملة."]},
                                    ensure_ascii=False), "end_turn"
        payload = json.dumps({"content": [{"type": "text", "text": text}],
                              "stop_reason": stop, "usage": {"output_tokens": 1}})
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(payload.encode())

    def log_message(self, *args):
        pass


def test_truncated_batch_is_retried_with_a_larger_budget():
    httpd = HTTPServer(("127.0.0.1", 0), _TruncatingClaude)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    _TruncatingClaude.seen = []
    try:
        provider = AnthropicProvider(
            api_key="sk-test", base_url=f"http://127.0.0.1:{httpd.server_address[1]}")
        rewriter = TranscriptRewriter(provider, RewriteConfig(enabled=True))
        plan = rewriter.build_plan(_transcript(_segments(6)), [], "ملف")
    finally:
        httpd.shutdown()
    assert TranscriptRewriter.MAX_TOKENS in _TruncatingClaude.seen
    assert TranscriptRewriter.MAX_TOKENS_RETRY in _TruncatingClaude.seen
    assert rewriter.raw_batches == 0
    assert all(s.title == "إعداد الاستمارات" for s in plan.sections)
    assert_lossless(plan)


def test_non_json_reply_gets_a_second_chance():
    replies = iter(["عذرًا، هذا شرح بلا JSON.",
                    json.dumps({"title": "قسم", "paragraphs": ["فقرة."]},
                               ensure_ascii=False)])
    fake = FakeVision(lambda prompt, budget: next(replies))
    rewriter = TranscriptRewriter(fake, RewriteConfig(enabled=True, make_outline=False))
    plan = rewriter.build_plan(_transcript(_segments(3)), [], "ملف")
    assert len(fake.calls) == 2
    assert "JSON صالحًا" in fake.calls[1][0]
    assert rewriter.raw_batches == 0
    assert plan.sections[0].blocks[0].text == "فقرة."


def test_raw_fallback_is_paragraphed_and_titled_so_the_gate_sees_it():
    good = json.dumps({"title": "قسم سليم", "paragraphs": ["فقرة."]}, ensure_ascii=False)
    calls = {"n": 0}

    def respond(prompt, budget):
        calls["n"] += 1
        return good if calls["n"] > 2 else "لا JSON"

    rewriter = TranscriptRewriter(FakeVision(respond),
                                  RewriteConfig(enabled=True, make_outline=False,
                                                batch_chars=3500))
    segments = _segments(60, chars=120)
    plan = rewriter.build_plan(_transcript(segments), [], "ملف")
    assert rewriter.raw_batches == 1 and rewriter.total_batches > 1
    raw_section = plan.sections[0]
    lengths = [len(b.text) for b in raw_section.blocks if b.kind == "paragraph"]
    assert len(lengths) > 1 and max(lengths) <= 700
    assert "ترتيبي" in title_defects(raw_section.title)
    assert_lossless(plan)


def test_raw_paragraphs_break_at_segment_boundaries():
    paragraphs = raw_paragraphs(_segments(30, chars=100))
    assert len(paragraphs) >= 4
    assert all(len(p) <= 700 for p in paragraphs)
    assert sum(len(p) for p in paragraphs) >= 30 * 100


def test_time_stamp_titles_are_ordinal_for_the_gate():
    assert "ترتيبي" in title_defects("المقطع الزمني 00:05:10")
    assert "ترتيبي" in title_defects("القسم الثاني — من 00:05:10")
    assert not title_defects("إعداد الاستمارات في بيرفورم")


# ── ٣. الوضع المرئي يقسم الجزء بدل أن يسقط كلّه ─────────────────────

def _visual_reply(parts):
    ids = _sent_ids(parts)
    return json.dumps({"title": "دليل", "sections": [{
        "title": f"قسم {ids[0]}", "blocks": [
            {"type": "figure", "id": i, "caption": f"لقطة {i}"} for i in ids]}]},
        ensure_ascii=False)


def test_truncated_visual_part_is_split_and_retried(tmp_path):
    frames = _frames(tmp_path, 8)

    def respond(parts, budget):
        if isinstance(parts, str):
            return json.dumps({"title": "دليل", "abstract": "", "key_points": []})
        if len(_sent_ids(parts)) > 4:
            raise ResponseTruncatedError("قُطع", partial='{"sections": [')
        return _visual_reply(parts)

    fake = FakeVision(respond)
    author = VisualAuthor(fake, VisualAuthorConfig(max_images_per_call=8))
    plan = author.build_plan(_transcript(_segments(8, step=10.0)), frames,
                             tmp_path, "ملف")
    image_calls = [c for c in fake.calls if isinstance(c[0], list)]
    assert [len(_sent_ids(c[0])) for c in image_calls] == [8, 4, 4]
    assert [s.title for s in plan.sections] == ["قسم 1", "قسم 5"]
    assert_lossless(plan)


def test_malformed_visual_part_is_split_too(tmp_path):
    frames = _frames(tmp_path, 6)

    def respond(parts, budget):
        if len(_sent_ids(parts)) > 3:
            return "ليس JSON"
        return _visual_reply(parts)

    plan = VisualAuthor(FakeVision(respond), VisualAuthorConfig(
        max_images_per_call=6)).build_plan(
        _transcript(_segments(6, step=10.0)), frames, tmp_path, "ملف")
    assert len(plan.sections) == 2
    assert_lossless(plan)


def test_small_parts_still_fail_to_the_text_path(tmp_path):
    frames = _frames(tmp_path, 3)
    fake = FakeVision(lambda parts, budget: "ليس JSON")
    with pytest.raises(RewriteUnavailableError):
        VisualAuthor(fake, VisualAuthorConfig(max_images_per_call=3)).build_plan(
            _transcript(_segments(3)), frames, tmp_path, "ملف")
    assert len(fake.calls) == 1


def test_visual_defaults_leave_room_for_arabic_output():
    config = VisualAuthorConfig()
    assert config.max_images_per_call <= 12
    assert config.max_tokens >= 16000
    from config.settings import SUPERSEDED_DEFAULTS, RewriteSettings

    assert RewriteSettings().max_images_per_call <= 12
    assert 20 in SUPERSEDED_DEFAULTS["rewrite"]["max_images_per_call"]


# ── ٤. الردود غير المستعملة تُحفظ للتشخيص ────────────────────────────

def test_unusable_replies_are_saved_in_the_job_folder(tmp_path):
    set_response_dump_dir(tmp_path)
    try:
        dump_failed_response("visual_part1_00:00:00", "نصّ مقطوع {", "قُطع")
    finally:
        set_response_dump_dir(None)
    saved = list((tmp_path / "ai_debug").glob("*.txt"))
    assert len(saved) == 1
    assert "نصّ مقطوع" in saved[0].read_text(encoding="utf-8")
    dump_failed_response("x", "y")                  # بلا مجلد: لا شيء ولا خطأ


# ── ٥. الحزمة التعليمية تستعمل إعداد الصياغة للمزوّد نفسه ─────────────

def test_study_pack_borrows_the_rewrite_key_for_the_same_provider():
    from core.pipeline_outputs import OutputsMixin

    rewrite = SimpleNamespace(enabled=True, provider="anthropic", model="",
                              base_url="", api_key_env="NO_SUCH_ENV_VAR",
                              api_key="sk-from-rewrite", workspace_id="")
    study = SimpleNamespace(provider="anthropic", use_rewrite_provider_as_fallback=True)
    host = SimpleNamespace(config=SimpleNamespace(rewrite=rewrite))
    provider = OutputsMixin._rewrite_provider_for_study(host, study)
    assert provider is not None and provider.api_key == "sk-from-rewrite"

    rewrite.enabled = False
    assert OutputsMixin._rewrite_provider_for_study(host, study) is None


# ── ٦. الخصوصية: أسماء المشاركين ولوحتهم ────────────────────────────

def _words(lines):
    words = []
    for number, line in enumerate(lines):
        left = 30 + 41 * number
        for index, text in enumerate(line.split()):
            words.append({"text": text, "line": ("1", "1", str(number)),
                          "left": left + 70 * index, "top": 20 * number,
                          "width": 60, "height": 12, "conf": 90.0})
    return words


@pytest.mark.parametrize("lines, hidden", [
    (["Shoub... Z Ali Ma... Z"], {"Shoub...", "Ali", "Ma..."}),
    (["Mohame... Hesha"], {"Mohame...", "Hesha"}),
    (["My Staff Hanaa"], {"Hanaa"}),
    (["Signed by Mohamad Ahmad on 08/31/2026 at 12:47 PM"], {"Mohamad", "Ahmad"}),
    (["Current User", "Mohamad Ahmad"], {"Mohamad", "Ahmad"}),
    (["Signature: Maged"], {"Maged"}),
])
def test_meeting_and_form_names_are_hidden(lines, hidden):
    from video.redact import find_name_boxes

    boxes, _names = find_name_boxes(_words(lines))
    assert hidden <= {b["text"] for b in boxes}


def test_ui_text_of_the_same_screens_is_left_alone():
    from video.redact import find_name_boxes

    lines = ["Teacher Evaluation Process", "Walkthrough Evidence Folder",
             "Form Schedule Assign", "Performance Matters", "he said that"]
    boxes, names = find_name_boxes(_words(lines))
    assert boxes == [] and names == []


def test_scrubbing_a_short_name_does_not_eat_other_words():
    from video.redact import scrub_text

    text = scrub_text("Alignment Ali\nMohame... Hesha\nZIP", ["Ali", "Mohame...", "Hesha"])
    assert "Alignment" in text and "ZIP" in text
    assert "Hesha" not in text and "Ali\n" not in text


def test_a_participant_name_is_never_a_caption():
    frame = SimpleNamespace(timestamp=100.0, ocr_text=(
        "Mohame... Hesha\nMy Staff Hanaa\nTeacher Evaluation Process"))
    assert _figure_caption(frame).startswith("Teacher Evaluation Process")


def _teams_frames(count=6):
    rng = np.random.default_rng(1)
    frames = []
    for _ in range(count):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        frame[:980, :1670] = 245                                # الشاشة المشارَكة
        frame[100:900, 200:1500] = rng.integers(0, 255, (800, 1300, 3))
        for top in range(40, 1000, 110):                        # بطاقات المشاركين
            frame[top:top + 70, 1720:1790] = rng.integers(60, 255, (70, 70, 3))
            frame[top:top + 70, 1830:1900] = rng.integers(60, 255, (70, 70, 3))
        frames.append(frame)
    return frames


def test_teams_participant_panel_and_stage_bar_are_cropped():
    from video.screen_crop import detect

    box = detect(_teams_frames())
    assert 240 <= box.right <= 260            # 1920 − 1670
    assert 90 <= box.bottom <= 110            # 1080 − 980
    assert box.left == 0
    cropped = box.apply(_teams_frames(1)[0])
    assert cropped.shape[:2] == (1080 - box.top - box.bottom, 1670)


def test_dark_themed_apps_are_not_mistaken_for_a_stage():
    from video.screen_crop import detect

    frames = []
    rng = np.random.default_rng(2)
    for _ in range(6):
        frame = np.full((1080, 1920, 3), 30, dtype=np.uint8)    # ثيم داكن ‎#1e1e1e
        frame[:, :300] = 18                                     # شريط جانبي ‎#121212
        frame[150:900, 400:1800] = rng.integers(0, 255, (750, 1400, 3))
        frames.append(frame)
    box = detect(frames)
    assert box.left == 0 and box.right == 0


def test_crop_box_is_not_applied_to_a_different_width():
    from video.keyframe_selector import KeyframeSelector
    from video.screen_crop import CropBox

    selector = KeyframeSelector(crop_box=CropBox(right=250, height=1080, width=1920))
    frame = np.zeros((1080, 1280, 3), dtype=np.uint8)
    assert selector._crop(frame).shape[1] == 1280


# ── ٧. المسرد بلا أزرار ولا شظايا ولا أسماء ─────────────────────────

def test_glossary_drops_buttons_fragments_names_and_table_headers():
    from ai.study_terms import _is_noise_term

    for noise in ("Start", "Folder", "SUPPORT", "pervisor", "Reset Filters",
                  "Task Sched Comp Responsible Resp Type", "Mohamed Ali"):
        assert _is_noise_term(noise, "screen"), noise
    for term in ("Teacher Evaluation Process", "Walkthrough Evidence Folder",
                 "MCOT", "Tenure Level", "Perform"):
        assert not _is_noise_term(term, "screen"), term


# ── ٨. فهرس الأشكال لا يبتلع صفحات ─────────────────────────────────

def test_figure_index_is_skipped_for_very_long_recordings():
    from document.word_generator import MAX_INDEX_FIGURES, MIN_INDEX_FIGURES

    assert MIN_INDEX_FIGURES < MAX_INDEX_FIGURES < 128


# ── ٩. فحص الوفاء لا يُنذر على اسمٍ منطوق بالعربية ───────────────────

def test_spoken_arabic_spelling_supports_the_latin_name():
    from config.schemas import DocumentBlock, DocumentPlan, DocumentSection
    from document.quality import unsupported_tokens

    plan = DocumentPlan(title="t", sections=[DocumentSection(
        title="قسم", blocks=[DocumentBlock(
            kind="paragraph", text="التسجيل متاح عبر SharePoint وتطبيق Teams و Moodle.")])])
    segments = [AudioSegment(id=1, start=0, end=1, text_raw="",
                             text_clean="هيكون على الشير بوينت وعلى التيمز")]
    assert unsupported_tokens(plan, segments)["latin"] == ["Moodle"]


# ── ١٠. شريط المتصفّح داخل الشاشة المشارَكة ─────────────────────────

def _browser_frame(height=600, width=1000, chrome=58):
    frame = np.full((height, width, 3), 250, dtype=np.uint8)   # شريط المتصفّح
    frame[chrome:chrome + 35] = 70                               # ترويسة التطبيق
    frame[chrome + 35:] = 245                                    # الصفحة
    return frame


def test_browser_bar_is_cut_below_the_url_and_bookmarks_lines():
    from video.screen_crop import _browser_bottom

    lines = [(0, 8, "tab title"), (19, 32, "https://maarif.tedk12.com/perform/Page.aspx?ID=5"),
             (40, 54, "MLG PowerSchool McGrawHill Schoology Master SchoolMessenger")]
    assert _browser_bottom(_browser_frame(), lines=lines) == 58


def test_no_url_line_means_no_browser_crop():
    from video.screen_crop import _browser_bottom

    lines = [(19, 32, "Perform Configuration"), (40, 54, "My Staff Tasks")]
    assert _browser_bottom(_browser_frame(), lines=lines) == 0


def test_a_url_inside_the_page_is_not_a_browser_bar():
    from video.screen_crop import _browser_bottom

    lines = [(300, 314, "visit www.school.edu.sa for details")]
    assert _browser_bottom(_browser_frame(), lines=lines) == 0


def test_browser_crop_needs_a_majority_of_frames(monkeypatch):
    import video.screen_crop as crop

    answers = iter([58, 0, 0])
    monkeypatch.setattr(crop, "_browser_bottom", lambda frame, lines=None: next(answers))
    assert crop.detect_browser_chrome([_browser_frame()] * 3) == 0
    answers = iter([58, 60, 0])
    assert crop.detect_browser_chrome([_browser_frame()] * 3) == 60
