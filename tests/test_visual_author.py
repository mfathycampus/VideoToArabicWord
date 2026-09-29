"""المؤلّف المرئي: مستندٌ من اللقطات والكلام معًا (ai/visual_author).

المزوّد هنا مزيّف يسجّل ما أُرسل إليه ويعيد ردًّا مكتوبًا سلفًا، فتُختبر
الضمانات البرمجية لا جودة النموذج: شكل الرسالة، وحساب اللقطات، ونسبة
المقاطع، والسقوط عند الفشل.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from ai.providers import (
    AnthropicProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    ProviderInfo,
    RewriteUnavailableError,
    image_part,
    text_part,
)
from ai.visual_author import VisualAuthor, VisualAuthorConfig
from config.schemas import (
    AudioSegment,
    KeyframeMetadata,
    TranscriptionResult,
)
from document.planner import assert_lossless
from document.quality import evaluate


class FakeVision:
    info = ProviderInfo(name="fake", label_ar="مزيّف", is_local=True,
                        privacy_note="")
    supports_vision = True
    model = "fake-vision"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def is_available(self):
        return True

    def complete(self, system_prompt, user_prompt, max_tokens=2048, timeout=180):
        self.calls.append((system_prompt, user_prompt))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)


def _frames(tmp_path: Path, count: int) -> list[KeyframeMetadata]:
    frames = []
    for i in range(1, count + 1):
        name = f"{i:04d}.jpg"
        Image.new("RGB", (1920, 1080), (20 * i % 255, 120, 200)).save(tmp_path / name)
        frames.append(KeyframeMetadata(
            image_id=i, timestamp=10.0 * i, filename=name, scene_id=i,
            change_score=0.3, width=1920, height=1080,
            selection_reason="stable_frame"))
    return frames


def _transcript(texts: list[tuple[float, str]]) -> TranscriptionResult:
    segments = [AudioSegment(id=n, start=t, end=t + 4, text_raw=x, text_clean=x)
                for n, (t, x) in enumerate(texts, start=1)]
    return TranscriptionResult(language="ar", full_text_raw="", full_text_clean="",
                               segments=segments, words=[], engine={"name": "t"})


REPLY = {
    "doc_type": "procedure",
    "title": "متابعة تحضير المعلمين في Curriculum & Instruction",
    "abstract": "يشرح الدليل الوصول إلى صفحة متابعة التحضير.",
    "key_points": ["الدخول من قائمة التطبيقات", "اختيار المدرسة"],
    "sections": [
        {"title": "الوصول إلى الصفحة", "summary": "",
         "blocks": [
             {"type": "step", "text": "اضغط «Admin» في الشريط العلوي."},
             {"type": "figure", "id": 1, "caption": "قائمة Admin مفتوحة",
              "screen_text": "Admin | Lesson Feedback"},
             {"type": "step", "text": "اختر «Lesson Feedback»."}]},
        {"title": "اختيار المدرسة",
         "blocks": [
             {"type": "figure", "id": 3, "caption": "قائمة المدارس"},
             {"type": "figure", "id": 3, "caption": "تكرار يُرفض"},
             {"type": "figure", "id": 99, "caption": "معرّف لم يُرسل"},
             {"type": "note", "text": "تظهر المعلمات المسكّنات على المدرسة فقط."},
             {"type": "bullets", "items": ["Met", "Not Met"]},
             {"type": "weird", "text": "نوع مجهول يصير فقرة"}]},
    ],
    "omit": [2],
}


def test_message_interleaves_speech_and_images(tmp_path):
    frames = _frames(tmp_path, 3)
    fake = FakeVision([REPLY])
    VisualAuthor(fake).build_plan(
        _transcript([(1.0, "السلام عليكم"), (12.0, "ندخل على الادمن")]),
        frames, tmp_path, "متابعة تحضير المعلمين")

    _, parts = fake.calls[0]
    kinds = [p["type"] for p in parts]
    assert kinds.count("image") == 3
    # الكلام الذي قبل اللقطة الأولى يسبقها في الرسالة
    first_image = kinds.index("image")
    before = " ".join(p.get("text", "") for p in parts[:first_image])
    assert "السلام عليكم" in before and "[لقطة 1 · 00:00:10]" in before
    # الصورة مصغّرة: لا تُرسل بدقّتها الأصلية
    import base64
    import io
    data = next(p for p in parts if p["type"] == "image")["data"]
    with Image.open(io.BytesIO(base64.b64decode(data))) as image:
        assert image.width == 1400


def test_plan_accounts_for_every_frame_and_segment(tmp_path):
    frames = _frames(tmp_path, 4)       # 4 لم يذكره النموذج إطلاقًا
    transcript = _transcript([(1.0, "أ"), (12.0, "ب"), (31.0, "ج"), (45.0, "د")])
    plan = VisualAuthor(FakeVision([REPLY])).build_plan(
        transcript, frames, tmp_path, "ملف")

    assert_lossless(plan)
    used = [b.image_id for s in plan.sections for b in s.blocks if b.image_id]
    assert sorted(used) == [1, 3, 4]          # 4 أُعيد إلى موضعه الزمني
    assert plan.figures_omitted == [2]
    assert plan.total_figures_in == 4 and plan.total_figures_out == 3
    kinds = [b.kind for s in plan.sections for b in s.blocks]
    assert kinds.count("step") == 2 and "note" in kinds and "bullets" in kinds
    assert "paragraph" in kinds               # النوع المجهول
    assert plan.generated_by == "ai:fake/fake-vision+vision"
    assert plan.subtitle == "دليل إجرائي مصوّر"
    # ما قرأه النموذج على الشاشة يُحفظ على الشكل
    first = next(b for b in plan.sections[0].blocks if b.image_id == 1)
    assert "Lesson Feedback" in first.ocr_text

    report = evaluate(plan, frames, segments=transcript.segments,
                      duration_seconds=60)
    assert not report.violations
    # اسم النظام في العنوان وارد في نصّ الشاشة أو اسم الملف — لا إنذار
    flagged = report.diagnostics["unsupported_tokens"]["latin"]
    assert "Lesson" not in flagged and "Feedback" not in flagged


def test_silent_recording_uses_images_only(tmp_path):
    frames = _frames(tmp_path, 3)
    fake = FakeVision([REPLY])
    plan = VisualAuthor(fake).build_plan(
        _transcript([]), frames, tmp_path, "تسكين الطلبة")
    text = " ".join(p.get("text", "") for p in fake.calls[0][1])
    assert "بلا كلام مسموع" in text
    assert plan.total_segments_in == 0
    assert_lossless(plan)


def test_long_recording_is_split_and_outlined(tmp_path):
    frames = _frames(tmp_path, 5)
    def part(ids):
        return {"sections": [{"title": f"قسم {ids[0]}", "blocks": [
            {"type": "figure", "id": i, "caption": f"لقطة {i}"} for i in ids]}]}
    outline = {"title": "دليل كامل", "abstract": "ملخّص.", "key_points": ["أ"]}
    fake = FakeVision([part([1, 2]), part([3, 4]), part([5]), outline])
    plan = VisualAuthor(fake, VisualAuthorConfig(max_images_per_call=2)).build_plan(
        _transcript([(5.0, "بداية"), (41.0, "منتصف")]), frames, tmp_path, "ملف")

    assert len(fake.calls) == 4
    assert "الجزء 2 من 3" in fake.calls[1][1][0]["text"]
    assert isinstance(fake.calls[3][1], str)          # الملخّص نصّي
    assert plan.title == "دليل كامل"
    assert [s.title for s in plan.sections] == ["قسم 1", "قسم 3", "قسم 5"]
    assert_lossless(plan)


def test_invalid_reply_raises_for_fallback(tmp_path):
    frames = _frames(tmp_path, 2)
    with pytest.raises(RewriteUnavailableError):
        VisualAuthor(FakeVision(["ليس JSON"])).build_plan(
            _transcript([(1.0, "نص")]), frames, tmp_path, "ملف")


def test_degenerate_title_falls_back_to_filename(tmp_path):
    frames = _frames(tmp_path, 1)
    reply = {"title": "بدون عنوان", "sections": [
        {"title": "قسم", "blocks": [{"type": "figure", "id": 1, "caption": "x"}]}]}
    plan = VisualAuthor(FakeVision([reply])).build_plan(
        _transcript([]), frames, tmp_path, "أخذ الغياب")
    assert plan.title == "أخذ الغياب"


# ── المزوّدون: تحويل الأجزاء إلى صيغة كل واجهة ──────────────────────

def test_anthropic_image_blocks():
    content = AnthropicProvider._user_content(
        [text_part("مرحبا"), image_part("QUJD")])
    assert content[0] == {"type": "text", "text": "مرحبا"}
    assert content[1]["source"] == {"type": "base64", "media_type": "image/jpeg",
                                    "data": "QUJD"}
    assert AnthropicProvider._user_content("نص") == "نص"


def test_openai_image_url():
    content = OpenAICompatibleProvider._user_content([image_part("QUJD")])
    assert content[0]["image_url"]["url"] == "data:image/jpeg;base64,QUJD"


def test_ollama_text_model_refuses_images():
    text_model = OllamaProvider(model="qwen2.5:7b-instruct")
    assert not text_model.supports_vision
    with pytest.raises(RewriteUnavailableError):
        text_model._user_message([image_part("QUJD")])
    vision = OllamaProvider(model="qwen2.5vl:7b")
    assert vision.supports_vision
    message = vision._user_message([text_part("أ"), image_part("QUJD")])
    assert message["images"] == ["QUJD"] and message["content"] == "أ"


def test_egress_counts_images(monkeypatch):
    from ai import providers

    providers.reset_egress()
    provider = AnthropicProvider(api_key="k")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"content":[{"type":"text","text":"{}"}]}'

    monkeypatch.setattr(providers.urllib.request, "urlopen",
                        lambda *a, **k: Response())
    provider.complete("s", [text_part("t"), image_part("QUJD"), image_part("QUJD")])
    assert providers.egress_report()["images"] == 2


def test_policy_can_block_images_only(tmp_path):
    from config import policy as policy_mod
    from config.settings import AppConfig

    path = tmp_path / "policy.yaml"
    path.write_text("allow_cloud_images: false\n", encoding="utf-8")
    config = AppConfig()
    config.rewrite.enabled = True
    config.rewrite.provider = "anthropic"
    policy_mod.apply(config, policy_mod.load(path))
    assert config.rewrite.send_images is False
    assert config.rewrite.provider == "anthropic"     # النصّ ما زال مسموحًا


def test_anonymize_keeps_person_names_out_of_the_request(tmp_path):
    frames = _frames(tmp_path, 1)
    fake = FakeVision([REPLY])
    VisualAuthor(fake, VisualAuthorConfig(
        anonymize_names=True,
        glossary="Lesson Feedback، Alhanouf Alsubaie، Math Education، a.b@x.sa",
    )).build_plan(_transcript([]), frames, tmp_path, "ملف")
    text = " ".join(p.get("text", "") for p in fake.calls[0][1])
    assert "Lesson Feedback" in text and "Math Education" in text
    assert "Alhanouf" not in text and "a.b@x.sa" not in text
    assert "لا تكتب اسم أي شخص" in text


def test_pipeline_withholds_unredacted_images(tmp_path, monkeypatch):
    """إخفاء الأسماء مطلوب واللقطات لم تُموَّه ⇒ لا تُرسل، والصياغة نصّية."""
    from ai import providers
    from config.settings import AppConfig
    from core.pipeline_outputs import OutputsMixin

    frames = _frames(tmp_path, 2)
    text_reply = {"title": "قسم", "summary": "", "paragraphs": ["فقرة مكتوبة."]}
    fake = FakeVision([text_reply] * 5)
    fake.info = ProviderInfo(name="anthropic", label_ar="x", is_local=False,
                             privacy_note="")
    monkeypatch.setattr(providers, "provider_from_settings", lambda s: fake)

    class Host(OutputsMixin):
        def __init__(self):
            self.config = AppConfig()
            self.config.rewrite.enabled = True
            self.config.document.anonymize_names = True

    class Meta:
        has_video = True
        duration_seconds = 60.0

    plan = Host()._build_plan(
        _transcript([(1.0, "نص"), (15.0, "نص آخر")]), frames,
        tmp_path / "ملف.mp4", Meta(), lambda *a: None, images_dir=tmp_path)
    assert not plan.generated_by.endswith("+vision")
    assert all(isinstance(call[1], str) for call in fake.calls)   # لا صور

    for frame in frames:
        frame.redacted = True
    fake2 = FakeVision([REPLY])
    fake2.info = fake.info
    monkeypatch.setattr(providers, "provider_from_settings", lambda s: fake2)
    plan = Host()._build_plan(
        _transcript([(1.0, "نص")]), frames, tmp_path / "ملف.mp4", Meta(),
        lambda *a: None, images_dir=tmp_path)
    assert plan.generated_by.endswith("+vision")


def test_focus_box_is_normalised():
    from ai.visual_author import _focus_box

    assert _focus_box([0.0, 0.0, 1.0, 1.0]) is None          # الشاشة كلّها
    assert _focus_box("x") is None
    left, top, right, bottom = _focus_box([0.5, 0.5, 0.52, 0.51])
    assert right - left >= 0.30 - 1e-9 and bottom - top >= 0.25 - 1e-9  # لا تكبير مفرط
    assert 0.0 <= left and right <= 1.0 and bottom <= 1.0
    # مقلوب ومتجاوز للحدود
    box = _focus_box([0.9, 0.8, 0.2, 1.4])
    assert box[0] < box[2] and box[3] <= 1.0


def test_focus_crop_is_written_and_used(tmp_path):
    frames = _frames(tmp_path, 1)
    reply = {"sections": [{"title": "قسم", "blocks": [
        {"type": "figure", "id": 1, "caption": "قائمة", "focus": [0.6, 0.0, 0.9, 0.4]}]}]}
    plan = VisualAuthor(FakeVision([reply])).build_plan(
        _transcript([]), frames, tmp_path, "ملف")
    block = plan.sections[0].blocks[0]
    assert block.image_filename != "0001.jpg" and "_focus_" in block.image_filename
    with Image.open(tmp_path / block.image_filename) as crop:
        assert crop.width < 1920 and crop.height < 1080
    assert (tmp_path / "0001.jpg").exists()                   # الأصل باقٍ
