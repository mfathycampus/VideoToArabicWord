"""1.19.4: التقدير المسبق من سرعة هذا الجهاز لا من معامل قِيس على جهاز آخر."""
import pytest

from core.stats import JobFact, measured_processing_ratio


def _fact(elapsed, duration=3600.0, status="completed",
          engine="faster-whisper", model="large-v3-turbo"):
    return JobFact(key="k", at="2026-11-01T10:00:00", status=status,
                   duration_seconds=duration, elapsed_seconds=elapsed,
                   engine=engine, model=model)


def test_none_without_matching_history():
    assert measured_processing_ratio([], engine="faster-whisper",
                                     model="large-v3-turbo") is None
    other = [_fact(9000, model="medium"), _fact(9000, status="failed")]
    assert measured_processing_ratio(other, engine="faster-whisper",
                                     model="large-v3-turbo") is None


def test_median_resists_rebuilds_and_long_pauses():
    # 2.5× ثلاث مرّات، وإعادة بناءٍ سريعة، وإيقافٌ مؤقّت ليلة كاملة
    facts = [_fact(9000), _fact(9000), _fact(9000), _fact(60), _fact(40000)]
    speed = measured_processing_ratio(facts, engine="faster-whisper",
                                      model="large-v3-turbo")
    assert speed.ratio == pytest.approx(2.5) and speed.jobs == 5


def test_short_clips_and_recent_window():
    facts = [_fact(500, duration=60)]                     # قصير: يُتجاهل
    facts += [_fact(36000)] * 4 + [_fact(3600)] * 8        # الجهاز تحسّن
    speed = measured_processing_ratio(facts, engine="faster-whisper",
                                      model="large-v3-turbo")
    assert speed.ratio == pytest.approx(1.0) and speed.jobs == 8
