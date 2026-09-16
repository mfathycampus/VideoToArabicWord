"""انحدارات رُصدت على مخرج حقيقي — لا على حالة متخيَّلة.

كل اختبار هنا يقابل عيبًا **وقع فعلًا** في تشغيل تسجيل «متابعة تحضير
المعلمين» (‏5:12، تسجيل شاشة عربي)، وكلّها مرّت من الاختبارات السابقة
كلّها ومن بوابة الجودة. وهذا هو معناها: مقياسٌ يمرّ عليه المخرجُ الفاشل
يُخفي العطب بدل أن يُظهره.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from config.settings import AppConfig, DocumentConfig


# ---------------------------------------------------------------------
# ١ · تطبيع الصوت — سبب ضياع 75٪ من الكلام
# ---------------------------------------------------------------------
def test_loudness_normalization_is_on_by_default():
    """‏15 dB من الهدوء أضاعت ثلاثة أرباع المفرَّغ."""
    assert AppConfig().application.normalize_audio is True


def test_normalization_is_independent_of_denoising():
    """كانا مقترنين، فبقي العلاج خلف مربّع لا يعرف المستخدم أنه يحتاجه."""
    from utils.ffmpeg_service import FFmpegService

    assert "loudnorm" in FFmpegService.LOUDNORM_FILTER
    assert "loudnorm" not in FFmpegService.DENOISE_FILTER
    assert "afftdn" in FFmpegService.DENOISE_FILTER


def test_denoise_is_applied_before_normalization(monkeypatch, tmp_path):
    """التنظيف ثم التسوية: تطبيعٌ يسبق خفض الضوضاء يرفع الضوضاء معه."""
    from utils.ffmpeg_service import FFmpegService

    captured = {}

    class _Result:
        returncode = 0
        stderr = ""

    service = FFmpegService.__new__(FFmpegService)
    target = tmp_path / "a.wav"

    def fake_run(args, **kwargs):
        captured["args"] = args
        target.write_bytes(b"RIFF")
        return _Result()

    monkeypatch.setattr(service, "_run", fake_run, raising=False)
    service.extract_audio(tmp_path / "v.mp4", target,
                          denoise=True, normalize=True)
    chain = captured["args"][captured["args"].index("-af") + 1]
    assert chain.index("afftdn") < chain.index("loudnorm")


def test_normalization_can_be_switched_off(monkeypatch, tmp_path):
    from utils.ffmpeg_service import FFmpegService

    captured = {}

    class _Result:
        returncode = 0
        stderr = ""

    service = FFmpegService.__new__(FFmpegService)
    target = tmp_path / "a.wav"

    def fake_run(args, **kwargs):
        captured["args"] = args
        target.write_bytes(b"RIFF")
        return _Result()

    monkeypatch.setattr(service, "_run", fake_run, raising=False)
    service.extract_audio(tmp_path / "v.mp4", target,
                          denoise=False, normalize=False)
    assert "-af" not in captured["args"]


# ---------------------------------------------------------------------
# ٢ · ادّعاء الصياغة
# ---------------------------------------------------------------------
def test_a_parseable_but_empty_reply_counts_as_a_failure():
    """العطل بعينه: ``failed`` كانت تُرفع عند الاستثناء وحده.

    فردٌّ سليم الشكل وفارغ المحتوى كان يُعدّ نجاحًا، فلا يُطلق حارس
    «فشلت كل الدفعات»، فيخرج المستند خامًا موسومًا ``ai:`` ويُطبع على
    غلافه «صياغة النص: ai:anthropic/claude-sonnet-5».
    """
    from ai.providers import LLMProvider, ProviderInfo
    from ai.rewriter import RewriteConfig, TranscriptRewriter
    from ai.providers import RewriteUnavailableError
    from config.schemas import AudioSegment, TranscriptionResult

    class Empty(LLMProvider):
        info = ProviderInfo(name="fake", label_ar="وهمي", is_local=True,
                            privacy_note="")
        model = "m"

        def is_available(self):
            return True

        def complete(self, *a, **k):
            return '{"title": "عنوان", "paragraphs": []}'

    segments = [AudioSegment(id=i, start=float(i * 5), end=float(i * 5 + 4),
                             text_raw="نصّ تجريبي للاختبار هنا.",
                             text_clean="نصّ تجريبي للاختبار هنا.")
                for i in range(1, 5)]
    body = " ".join(s.text_clean for s in segments)
    transcript = TranscriptionResult(language="ar", full_text_raw=body,
                                     full_text_clean=body, segments=segments)

    with pytest.raises(RewriteUnavailableError) as caught:
        TranscriptRewriter(Empty(), RewriteConfig(enabled=True)).build_plan(
            transcript, [], "اسم الملفّ")
    # والسبب يُرفع مع الاستثناء: «تعذّرت» بلا سبب لا يُمكّن المستخدم
    assert "فشلت كل دفعات" in str(caught.value)
    assert len(str(caught.value)) > 40


@pytest.mark.parametrize("title", [
    "مقطع زمني بدون محتوى", "بلا محتوى", "Untitled", "غير محدد",
])
def test_a_title_describing_emptiness_is_rejected(title):
    """خرج هذا حرفيًّا على غلاف مستند حقيقي وفي رأس كل صفحة."""
    from ai.rewriter import _is_degenerate_title

    assert _is_degenerate_title(title)


def test_a_real_title_survives_the_guard():
    from document.planner import _safe_title

    assert _safe_title("متابعة تحضير المعلمين") == "متابعة تحضير المعلمين"
    assert _safe_title("مقطع زمني بدون محتوى") == "تفريغ تسجيل"


# ---------------------------------------------------------------------
# ٣ · سجلّ التدقيق — ADR-020
# ---------------------------------------------------------------------
def test_audit_says_no_egress_when_the_provider_never_connected(
        tmp_path, monkeypatch):
    """العطل بعينه: الإعداد ``anthropic`` والمزوّد سقط قبل أن يُرسل،
    وسطر التدقيق قال إن نصّ المحاضرة غادر الجهاز."""
    import json

    from ai import providers
    from utils import audit

    monkeypatch.setenv("VTAD_AUDIT_DIR", str(tmp_path / "log"))
    config = AppConfig()
    config.rewrite.enabled = True
    config.rewrite.provider = "anthropic"

    source = tmp_path / "د.mp4"
    source.write_bytes(b"x")
    with audit.record_job(config, source, tmp_path / "job"):
        providers.record_egress("anthropic", False, "URLError")

    line = json.loads((tmp_path / "log" / audit.AUDIT_FILENAME)
                      .read_text(encoding="utf-8").strip())
    assert line["text_left_device"] is False
    assert line["cloud_providers"] == []
    assert line["permitted_providers"] == ["anthropic"]   # النيّة تُذكر
    assert line["attempted_no_egress"] is True            # ولا تُخفى المحاولة


def test_audit_says_egress_when_the_request_actually_left(tmp_path, monkeypatch):
    import json

    from ai import providers
    from utils import audit

    monkeypatch.setenv("VTAD_AUDIT_DIR", str(tmp_path / "log"))
    config = AppConfig()
    config.study.enabled = True
    config.study.provider = "anthropic"
    source = tmp_path / "د.mp4"
    source.write_bytes(b"x")
    with audit.record_job(config, source, tmp_path / "job"):
        providers.record_egress("anthropic", True)

    line = json.loads((tmp_path / "log" / audit.AUDIT_FILENAME)
                      .read_text(encoding="utf-8").strip())
    assert line["text_left_device"] is True
    assert line["cloud_providers"] == ["anthropic"]


def test_a_rejected_request_still_counts_as_egress(tmp_path, monkeypatch):
    """ردٌّ بـ401 يعني أن الطلب **وصل** — أي أن النصّ غادر ولو رُفض."""
    import json

    from ai import providers
    from utils import audit

    monkeypatch.setenv("VTAD_AUDIT_DIR", str(tmp_path / "log"))
    source = tmp_path / "د.mp4"
    source.write_bytes(b"x")
    with audit.record_job(AppConfig(), source, tmp_path / "job"):
        providers.record_egress("anthropic", True, "HTTP 401")

    line = json.loads((tmp_path / "log" / audit.AUDIT_FILENAME)
                      .read_text(encoding="utf-8").strip())
    assert line["text_left_device"] is True


def test_egress_from_a_previous_job_does_not_leak_into_the_next(
        tmp_path, monkeypatch):
    import json

    from ai import providers
    from utils import audit

    monkeypatch.setenv("VTAD_AUDIT_DIR", str(tmp_path / "log"))
    source = tmp_path / "د.mp4"
    source.write_bytes(b"x")
    with audit.record_job(AppConfig(), source, tmp_path / "j1"):
        providers.record_egress("anthropic", True)
    with audit.record_job(AppConfig(), source, tmp_path / "j2"):
        pass

    lines = [json.loads(ln) for ln in
             (tmp_path / "log" / audit.AUDIT_FILENAME)
             .read_text(encoding="utf-8").strip().splitlines()]
    assert [ln["text_left_device"] for ln in lines] == [True, False]


# ---------------------------------------------------------------------
# ٤ · قصّ زينة الشاشة — تسريب الخصوصية
# ---------------------------------------------------------------------
def _screen_frames(height=1040, width=200, chrome=84, taskbar=73, count=6):
    """إطارات تحاكي تسجيل شاشة: زينة ثابتة ومحتوى متغيّر."""
    import numpy as np

    frames = []
    for index in range(count):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:chrome] = 240                       # شريط المتصفّح — ثابت
        frame[height - taskbar:] = 30              # شريط المهام — ثابت
        rng = np.random.default_rng(index)
        frame[chrome:height - taskbar] = rng.integers(
            0, 255, (height - chrome - taskbar, width, 3), dtype=np.uint8)
        frames.append(frame)
    return frames


def test_static_chrome_rows_are_detected_and_cropped():
    from video.screen_crop import detect

    box = detect(_screen_frames())
    assert box.active
    assert 70 <= box.top <= 95        # الزينة عند الصفّ 84
    assert 60 <= box.bottom <= 90     # وشريط المهام 73


def test_a_still_recording_is_never_cropped():
    """صفحةٌ ساكنة حقًّا تبدو للكاشف زينةً — وقصُّها يحذف الدرس."""
    import numpy as np

    from video.screen_crop import detect

    still = [np.full((600, 200, 3), 128, dtype=np.uint8) for _ in range(6)]
    assert detect(still).active is False


def test_cropping_never_exceeds_the_safety_caps():
    """الخطأ هنا في اتجاه واحد: ترك شريطٍ أهون من حذف محتوى."""
    from video.screen_crop import MAX_BOTTOM_RATIO, MAX_TOP_RATIO, detect

    box = detect(_screen_frames(height=1000, chrome=600, taskbar=300))
    assert box.top <= 1000 * MAX_TOP_RATIO
    assert box.bottom <= 1000 * MAX_BOTTOM_RATIO


def test_too_few_frames_means_no_guessing():
    from video.screen_crop import detect

    assert detect(_screen_frames(count=2)).active is False


def test_crop_is_skipped_when_the_height_does_not_match():
    """صندوقٌ عُوير على ارتفاع آخر لا يُطبَّق بالتخمين."""
    import numpy as np

    from video.keyframe_selector import KeyframeSelector
    from video.screen_crop import CropBox

    selector = KeyframeSelector(crop_box=CropBox(top=80, bottom=70, height=1040))
    frame = np.zeros((720, 200, 3), dtype=np.uint8)
    assert selector._crop(frame).shape[0] == 720


def test_crop_applies_when_the_height_matches():
    import numpy as np

    from video.keyframe_selector import KeyframeSelector
    from video.screen_crop import CropBox

    selector = KeyframeSelector(crop_box=CropBox(top=80, bottom=70, height=1040))
    frame = np.zeros((1040, 200, 3), dtype=np.uint8)
    assert selector._crop(frame).shape[0] == 1040 - 150


def test_screen_cropping_is_on_by_default():
    assert AppConfig().frames.crop_screen_chrome is True


# ---------------------------------------------------------------------
# ٥ · عناوين من نصّ الشاشة
# ---------------------------------------------------------------------
def test_an_application_navigation_bar_is_not_a_title():
    """خرج هذا عنوانَ قسمٍ في فهرس مستند حقيقي."""
    from document.titles import clean_title_candidate

    assert clean_title_candidate(
        "Curriculum & Instruction Lessons v Curriculum Reports »") == ""


def test_a_trailing_ocr_fragment_is_stripped_not_the_whole_title():
    """«… WEEK 4 tte» عنوانٌ سليم تذيّلته قراءة فاشلة — يُشذَّب لا يُرفض."""
    from document.titles import clean_title_candidate

    assert (clean_title_candidate("GRADE 9AM WEEKLY PLAN - WEEK 4 tte")
            == "GRADE 9AM WEEKLY PLAN - WEEK 4")


def test_a_real_short_word_at_the_end_survives():
    from document.titles import clean_title_candidate

    assert clean_title_candidate("Lesson Feedback by AM").endswith("AM")


# ---------------------------------------------------------------------
# ٦ · بنية المستند والتعليقات
# ---------------------------------------------------------------------
def test_a_sparse_transcript_is_not_collapsed_into_one_batch():
    """‏597 حرفًا لفيديو 5:12 صارت دفعةً واحدة — أي قسمًا وحيدًا."""
    from ai.rewriter import RewriteConfig, TranscriptRewriter
    from config.schemas import AudioSegment

    segments = [AudioSegment(id=i, start=float(i * 8), end=float(i * 8 + 3),
                             text_raw="جملة قصيرة من التفريغ.",
                             text_clean="جملة قصيرة من التفريغ.")
                for i in range(1, 28)]          # ≈ 590 حرفًا
    rewriter = TranscriptRewriter(None, RewriteConfig(enabled=True))
    assert len(rewriter._batch(segments)) >= 3


def test_an_index_that_cannot_fill_a_page_is_not_emitted():
    """سبع صفحات، اثنتان منها فهرسان بسطرٍ واحد لكلٍّ."""
    from document.word_generator import MIN_INDEX_FIGURES, MIN_TOC_SECTIONS

    assert MIN_TOC_SECTIONS >= 3
    assert MIN_INDEX_FIGURES >= 4


def test_screen_glossary_autofill_is_on_and_never_overrides_the_user():
    from config.settings import TranscriptionConfig

    assert TranscriptionConfig().auto_screen_glossary is True


def test_figure_caption_falls_back_to_screen_text_not_to_emptiness():
    """أربع صور بتعليق فارغ بينما نصّ الشاشة لكلٍّ منها محفوظ."""
    from config.schemas import KeyframeMetadata
    from document.planner import _figure_caption

    keyframe = KeyframeMetadata(
        image_id=1, timestamp=76.1, filename="f.jpg", scene_id=1,
        change_score=0.3, width=10, height=10, selection_reason="stable",
        ocr_text="Islamic Education\nPlanned\nHoly Quran")
    caption = _figure_caption(keyframe)
    assert "Islamic Education" in caption
    assert caption != ""


# ---------------------------------------------------------------------
# قياس مستوى الصوت كان ميتًا: ``-loglevel error`` يُخفي ``volumedetect``
# ---------------------------------------------------------------------
def test_audio_levels_asks_ffmpeg_for_info_level_output(monkeypatch):
    """‏``volumedetect`` يطبع ``max_volume`` بمستوى info.

    ‏``_run`` كان يفرض ``-loglevel error`` على كل أمر، فيعود stderr بلا
    أرقام، فتعيد ``audio_levels`` ‏None دائمًا، فلا يظهر في السجل تحذير
    «المصدر هادئ» أبدًا — في التشغيل الحقيقي نفسه الذي أُضيف من أجله.
    """
    import subprocess

    from utils.ffmpeg_service import FFmpegService

    captured = {}

    class Result:
        returncode = 0
        stderr = ("[Parsed_volumedetect_0 @ 0x1] mean_volume: -36.5 dB\n"
                  "[Parsed_volumedetect_0 @ 0x1] max_volume: -26.6 dB\n")

    def fake_subprocess_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return Result()

    service = FFmpegService()
    service._exe = "ffmpeg"
    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    levels = service.audio_levels(Path("v.mp4"))

    cmd = captured["cmd"]
    assert cmd[cmd.index("-loglevel") + 1] == "info"
    assert levels == {"peak_db": -26.6, "mean_db": -36.5}


def test_other_ffmpeg_commands_stay_quiet(monkeypatch, tmp_path):
    """التغيير لا يُغرق بقية الأوامر بمخرجات info."""
    import subprocess

    from utils.ffmpeg_service import FFmpegService

    captured = {}

    class Result:
        returncode = 0
        stderr = ""

    def fake_subprocess_run(cmd, **kwargs):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"x" * 16)
        return Result()

    service = FFmpegService()
    service._exe = "ffmpeg"
    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    service.extract_audio(tmp_path / "v.mp4", tmp_path / "a.wav")

    cmd = captured["cmd"]
    assert cmd[cmd.index("-loglevel") + 1] == "error"
