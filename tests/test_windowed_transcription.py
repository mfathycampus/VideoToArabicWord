"""1.21.1: الصوت الطويل يُفرَّغ نوافذ — E4001 «Unable to allocate 1.38 GiB»."""
import wave

import numpy as np

from audio.transcriber import TranscriptionEngine
from config.settings import TranscriptionConfig


class _Word:
    def __init__(self, word, start, end):
        self.word, self.start, self.end, self.probability = word, start, end, 0.9


class _Seg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text
        self.words = [_Word(text, start, end)]


class _Info:
    duration = 0.0
    language = "ar"


class _WindowModel:
    """يسجّل ما يُمرَّر إليه، ويعيد مقطعًا لكل ثانيتين من النافذة."""

    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **options):
        self.calls.append(audio)
        seconds = (len(audio) / 16000.0) if isinstance(audio, np.ndarray) else 4.0
        segs = [_Seg(t, t + 1.5, f"ك{int(t)}") for t in np.arange(0, seconds - 1.5, 2.0)]
        return iter(segs), _Info()


def _wav(path, seconds, quiet_at=()):
    rate = 16000
    t = np.arange(int(seconds * rate)) / rate
    signal = 0.3 * np.sin(2 * np.pi * 220 * t)
    for at in quiet_at:
        signal[int(at * rate):int((at + 0.5) * rate)] = 0.0
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((signal * 32767).astype(np.int16).tobytes())
    return path


def _engine(monkeypatch, model):
    engine = TranscriptionEngine(TranscriptionConfig(word_timestamps=True,
                                                     gap_refill=False))
    monkeypatch.setattr(engine, "_load_model", lambda *a, **k: model)
    engine.keep_model_loaded = True
    engine.WINDOW_SECONDS = 10.0
    engine.WINDOW_MIN_TOTAL = 15.0
    engine.WINDOW_SEARCH_SECONDS = 4.0
    return engine


def test_long_audio_is_fed_in_windows_with_absolute_times(monkeypatch, tmp_path):
    model = _WindowModel()
    audio = _wav(tmp_path / "a.wav", 34.0, quiet_at=(8.0, 19.0))
    result = _engine(monkeypatch, model).transcribe(audio)

    assert all(isinstance(c, np.ndarray) for c in model.calls)   # لا ملفّ كاملًا
    assert len(model.calls) >= 3
    assert max(len(c) for c in model.calls) <= 15 * 16000        # كل نافذة ≤ 1.5 × الحجم
    # الحدّ في أهدأ نصف ثانية لا عند الثانية العاشرة بالضبط
    first = len(model.calls[0]) / 16000.0
    assert 8.0 <= first <= 8.5
    starts = [sg.start for sg in result.segments]
    assert starts == sorted(starts) and starts[-1] > 25.0       # زمن الصوت الكامل
    assert result.segments[-1].words[0].start == result.segments[-1].start


def test_short_audio_keeps_the_single_call_path(monkeypatch, tmp_path):
    model = _WindowModel()
    audio = _wav(tmp_path / "a.wav", 6.0)
    _engine(monkeypatch, model).transcribe(audio)
    assert model.calls == [str(audio)]
