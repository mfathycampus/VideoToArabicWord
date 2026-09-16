"""سدّ فجوات التفريغ — ما يبتلعه Silero VAD يُعاد بلا VAD على الفجوة وحدها.

قياس «متابعة تحضير المعلمين»: VAD يغطّي 25.5٪ بفجوة 89 ثانية، وتعطيله
يغطّي 63.7٪ بأطول فجوة 11.8 ثانية — ونصّ الفجوة كلامٌ حقيقي.
"""
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pytest

pytest.importorskip("faster_whisper")
pytest.importorskip("ctranslate2")

from audio.transcriber import TranscriptionEngine
from config.schemas import AudioSegment
from config.settings import TranscriptionConfig


def _seg(start, end, text="نص"):
    return AudioSegment(id=0, start=start, end=end, text_raw=text, text_clean=text)


def _wav(path, seconds_and_levels, rate=16000):
    """‏[(ثوانٍ، سعة)] — سعة 0 صمت، و0.3 «كلام»."""
    chunks = []
    for seconds, amplitude in seconds_and_levels:
        t = np.arange(int(seconds * rate)) / rate
        chunks.append(amplitude * np.sin(2 * np.pi * 220 * t))
    data = (np.concatenate(chunks) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(data.tobytes())
    return path


class FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, samples, **options):
        self.calls.append((len(samples), options))
        piece = SimpleNamespace(start=1.0, end=4.0, text=" كلام ضاع ", words=[])
        return iter([piece]), SimpleNamespace()


def _engine(**overrides):
    config = TranscriptionConfig(download_root=Path("."), **overrides)
    return TranscriptionEngine(config)


def test_find_gaps_includes_leading_and_trailing_silence():
    gaps = TranscriptionEngine.find_gaps(
        [_seg(20, 30), _seg(31, 40), _seg(120, 130)], 0, 150, 12)
    assert gaps == [(0, 20), (40, 120), (130, 150)]


def test_audible_gaps_are_retranscribed_without_vad(tmp_path):
    audio = _wav(tmp_path / "a.wav", [(10, 0.3), (30, 0.3), (10, 0.3)])
    engine, model = _engine(), FakeModel()

    added = engine._refill_gaps(
        model, {"vad_filter": True, "vad_parameters": {"threshold": 0.5},
                "beam_size": 1},
        audio, [_seg(0, 10), _seg(40, 50)], 0.0, 50.0, None, None)

    assert len(model.calls) == 1
    samples, options = model.calls[0]
    assert samples == 30 * 16000
    assert options["vad_filter"] is False and options["vad_parameters"] is None
    assert options["beam_size"] == 1
    # الأزمنة بزمن الملفّ لا بزمن المقطع المقتطع
    assert added[0].start == pytest.approx(11.0)
    assert added[0].end == pytest.approx(14.0)
    assert added[0].text_clean == "كلام ضاع"


def test_silent_gaps_are_not_retranscribed(tmp_path):
    """الصمت الحقيقي بلا VAD بابٌ للهلوسة («شكرًا لكم»)."""
    audio = _wav(tmp_path / "a.wav", [(10, 0.3), (30, 0.0), (10, 0.3)])
    engine, model = _engine(), FakeModel()

    added = engine._refill_gaps(model, {}, audio, [_seg(0, 10), _seg(40, 50)],
                                0.0, 50.0, None, None)
    assert added == [] and model.calls == []


def test_short_gaps_are_left_alone(tmp_path):
    audio = _wav(tmp_path / "a.wav", [(30, 0.3)])
    engine, model = _engine(gap_refill_min_seconds=12), FakeModel()
    engine._refill_gaps(model, {}, audio, [_seg(0, 10), _seg(20, 30)],
                        0.0, 30.0, None, None)
    assert model.calls == []


def test_resumed_offset_shifts_refilled_segments(tmp_path):
    """عند الاستئناف الملفّ بقيّةٌ تبدأ من الصفر، والمقاطع بزمن الصوت الكامل."""
    audio = _wav(tmp_path / "rest.wav", [(40, 0.3)])
    engine, model = _engine(), FakeModel()
    added = engine._refill_gaps(model, {}, audio,
                                [_seg(1000, 1005), _seg(1025, 1040)],
                                1000.0, 40.0, None, None)
    assert added[0].start == pytest.approx(1006.0)


def test_non_wav_input_disables_refill_quietly(tmp_path):
    fake = tmp_path / "a.mp3"
    fake.write_bytes(b"ID3 not a wav")
    engine, model = _engine(), FakeModel()
    assert engine._refill_gaps(model, {}, fake, [_seg(20, 30)],
                               0.0, 30.0, None, None) == []
