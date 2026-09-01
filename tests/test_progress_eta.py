"""تقدير المدة والوقت المتبقي.

انحدار على تجربة مستخدم حقيقية: محاضرة 81 دقيقة على المعالج تحتاج ساعات،
وشريط التقدّم يزحف ببطء شديد. بلا تقدير ظاهر يظن المستخدم أن البرنامج
متجمد فيلغي عملًا سليمًا.
"""
import pytest

from audio.model_manager import (
    AVAILABLE_MODELS, DEFAULT_MODEL, MEASURED_RTF_CPU,
    estimate_processing_seconds,
)
from utils.timestamps import estimate_remaining, humanize_duration


def test_default_model_is_the_measured_best():
    """turbo تفوّق على large-v3 في الدقة والسرعة معًا على العربية."""
    assert DEFAULT_MODEL == "large-v3-turbo"
    assert AVAILABLE_MODELS[0].name == DEFAULT_MODEL, \
        "الموصى به يجب أن يكون أول خيار في القائمة"


def test_every_model_has_a_measured_speed():
    for spec in AVAILABLE_MODELS:
        assert spec.name in MEASURED_RTF_CPU, \
            f"{spec.name}: لا يوجد معامل زمن مقاس"
        assert MEASURED_RTF_CPU[spec.name] > 0


def test_turbo_is_faster_than_large_v3():
    assert MEASURED_RTF_CPU["large-v3-turbo"] < MEASURED_RTF_CPU["large-v3"]


def test_estimate_scales_with_audio_length():
    short = estimate_processing_seconds(DEFAULT_MODEL, 60)
    long = estimate_processing_seconds(DEFAULT_MODEL, 600)
    assert long == pytest.approx(short * 10, rel=0.01)


def test_gpu_estimate_is_faster_than_cpu():
    cpu = estimate_processing_seconds(DEFAULT_MODEL, 3600, on_gpu=False)
    gpu = estimate_processing_seconds(DEFAULT_MODEL, 3600, on_gpu=True)
    assert gpu < cpu / 4


def test_estimate_for_a_real_lecture_is_sane():
    """محاضرة 81.5 دقيقة — الحالة التي أثارت السؤال."""
    seconds = estimate_processing_seconds("large-v3-turbo", 4893)
    assert 3000 < seconds < 9000, f"تقدير غير معقول: {seconds}s"


def test_unknown_model_still_returns_an_estimate():
    assert estimate_processing_seconds("does-not-exist", 100) > 0


@pytest.mark.parametrize("seconds,expected", [
    (45, "ثانية"), (600, "دقيقة"), (3600, "ساعة"), (16290, "ساعة"),
])
def test_humanize_duration_is_arabic(seconds, expected):
    text = humanize_duration(seconds)
    assert expected in text
    assert not text.startswith("-")


def test_humanize_handles_zero_and_negative():
    assert humanize_duration(0) == "0 ثانية"
    assert humanize_duration(-5) == "0 ثانية"


def test_remaining_time_is_withheld_until_meaningful():
    """تقدير مبني على أول لحظة عمل عشوائي ويفقد ثقة المستخدم."""
    assert estimate_remaining(1.0, 0.0001) is None
    assert estimate_remaining(2.0, 0.5) is None      # زمن منقضٍ قصير جدًا
    assert estimate_remaining(0.0, 0.5) is None


def test_remaining_time_is_correct_when_available():
    # أُنجز الربع في 100 ثانية ⇒ يتبقى ~300 ثانية
    assert estimate_remaining(100.0, 0.25) == pytest.approx(300.0, rel=0.01)
    assert estimate_remaining(100.0, 1.0) == pytest.approx(0.0, abs=0.01)


def test_transcriber_progress_message_carries_eta(monkeypatch):
    """رسالة التقدّم يجب أن تحمل النسبة والدقائق والوقت المتبقي."""
    import time as time_module
    from utils.timestamps import estimate_remaining as est

    fraction = 0.25
    elapsed = 400.0
    remaining = est(elapsed, fraction)
    message = (f"التفريغ {fraction * 100:.1f}% "
               f"(20 من 80 دقيقة) · متبقٍ ~{humanize_duration(remaining)}")
    assert "25.0%" in message
    assert "دقيقة" in message
    assert "متبقٍ" in message
