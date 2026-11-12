"""``tools/eval_real.py`` — قياس الدقة على تسجيلات حقيقية.

لا نموذج ولا ffmpeg هنا: الاختيار والتقييم والصفحة تُختبر ببدائل محقونة.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import eval_real
from tools.eval_real import Clip, plan_clips, prefer_speech, render_review, score

DURATIONS = {"a.mkv": 148.0, "b.mkv": 114.0, "c.mkv": 2966.0, "d.mkv": 5845.0}


# ---------------------------------------------------------------------
# اختيار المقاطع
# ---------------------------------------------------------------------
def test_plan_is_reproducible_and_changes_with_the_seed():
    first = plan_clips(DURATIONS, 12 * 60, 30, seed=7)
    assert first == plan_clips(DURATIONS, 12 * 60, 30, seed=7)
    assert first != plan_clips(DURATIONS, 12 * 60, 30, seed=8)
    assert [clip.name for clip in first] == [f"clip_{i:02d}" for i in range(1, 25)]


def test_every_recording_is_sampled_and_long_ones_get_more():
    clips = plan_clips(DURATIONS, 12 * 60, 30)
    per_source = {name: sum(clip.source == name for clip in clips) for name in DURATIONS}
    assert all(count >= 1 for count in per_source.values())
    assert per_source["d.mkv"] > per_source["c.mkv"] > per_source["a.mkv"]
    assert sum(per_source.values()) == 24


def test_clips_never_overlap_and_stay_inside_the_recording():
    clips = plan_clips(DURATIONS, 20 * 60, 30)
    for name, duration in DURATIONS.items():
        mine = sorted((c for c in clips if c.source == name), key=lambda c: c.start)
        for clip in mine:
            assert 0 <= clip.start and clip.start + clip.duration <= duration
        for before, after in zip(mine, mine[1:]):
            assert before.start + before.duration <= after.start


def test_clips_spread_over_the_whole_recording_not_its_start():
    clips = [c for c in plan_clips({"d.mkv": 5845.0}, 10 * 60, 30)]
    starts = [c.start for c in clips]
    assert min(starts) < 5845 * 0.15 and max(starts) > 5845 * 0.85


def test_short_recordings_are_skipped_and_request_is_capped():
    assert plan_clips({"tiny.wav": 12.0}, 600, 30) == []
    clips = plan_clips({"one.wav": 70.0}, 600, 30)        # يتّسع لمقطعين فقط
    assert len(clips) == 2


def test_a_silent_clip_is_replaced_from_its_own_stratum_only():
    clips = plan_clips({"d.mkv": 5845.0}, 4 * 30, 30)      # 4 شرائح
    silent = clips[1]
    asked = []

    def ratio(source, start, duration):
        asked.append(start)
        return 0.1 if start == silent.start else 0.9

    fixed, weak = prefer_speech(clips, {"d.mkv": 5845.0}, ratio)
    assert weak == []
    assert fixed[0] == clips[0] and fixed[2:] == clips[2:]
    assert fixed[1].start != silent.start and fixed[1].name == silent.name
    low, high = eval_real.stratum_bounds(silent, {"d.mkv": 5845.0}, 4, 1)
    assert low <= fixed[1].start <= high


def test_a_stratum_with_no_speech_is_kept_and_flagged():
    clips = plan_clips({"d.mkv": 5845.0}, 2 * 30, 30)
    fixed, weak = prefer_speech(clips, {"d.mkv": 5845.0}, lambda *args: 0.0)
    assert weak == [clip.name for clip in clips]
    assert len(fixed) == len(clips)


# ---------------------------------------------------------------------
# prepare — ببدائل
# ---------------------------------------------------------------------
class _FakeFFmpeg:
    def __init__(self):
        self.extracted = []

    def _run(self, args, timeout=0, loglevel="error"):
        class Result:
            stderr = "silence_duration: 3.0\n"
        return Result()

    def extract_audio(self, source, output, start_seconds=0.0, end_seconds=None, **kw):
        self.extracted.append((Path(source).name, start_seconds, end_seconds))
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_bytes(b"RIFF")
        return output


def _folder(tmp_path):
    folder = tmp_path / "recordings"
    folder.mkdir()
    for name in ("c.mkv", "d.mkv", "notes.txt", "c.recording.json"):
        (folder / name).write_bytes(b"x")
    return folder


def test_prepare_writes_clips_manifest_and_review_page(tmp_path):
    folder, ffmpeg = _folder(tmp_path), _FakeFFmpeg()
    out = eval_real.prepare(
        folder, minutes=3, ffmpeg=ffmpeg, log=lambda _: None,
        durations={"c.mkv": 2966.0, "d.mkv": 5845.0},
        transcribe=lambda wav: f"نص {wav.stem}", model="test-model")
    assert out == folder / "_eval"
    manifest = json.loads((out / "eval_set.json").read_text(encoding="utf-8"))
    assert len(manifest["clips"]) == 6 == len(ffmpeg.extracted)
    assert {clip["source"] for clip in manifest["clips"]} == {"c.mkv", "d.mkv"}
    assert manifest["clips"][0]["hyp"] == "نص clip_01"
    assert manifest["model"] == "test-model"
    for (_, start, end) in ffmpeg.extracted:
        assert end - start == pytest.approx(30.0)
    assert (out / "clips" / "clip_06.wav").is_file()
    assert "نص clip_01" in (out / "review.html").read_text(encoding="utf-8")


def test_blank_mode_runs_no_model_and_leaves_boxes_empty(tmp_path):
    folder = _folder(tmp_path)

    def must_not_run(_):
        raise AssertionError("لا تفريغ في --blank")

    out = eval_real.prepare(folder, minutes=1, blank=True, ffmpeg=_FakeFFmpeg(),
                            durations={"c.mkv": 2966.0, "d.mkv": 5845.0},
                            transcribe=must_not_run, log=lambda _: None)
    manifest = json.loads((out / "eval_set.json").read_text(encoding="utf-8"))
    assert manifest["blank"] is True and manifest["model"] == ""
    assert all(clip["hyp"] == "" for clip in manifest["clips"])


def test_prepare_refuses_to_write_inside_the_repository(tmp_path):
    """المقاطع فيها أصوات آخرين — لا تُكتب حيث قد تدخل Git."""
    with pytest.raises(SystemExit) as caught:
        eval_real.prepare(_folder(tmp_path), out=eval_real.ROOT / "testdata" / "x",
                          ffmpeg=_FakeFFmpeg(), durations={"c.mkv": 2966.0},
                          transcribe=lambda _: "", log=lambda _: None)
    assert "المستودع" in str(caught.value)


def test_review_page_embeds_text_as_data_not_markup():
    clips = [{"name": "clip_01", "source": "a<b>.mkv", "start": 65.0, "duration": 30.0,
              "hyp": '</script><img src=x onerror="alert(1)">'}]
    page = render_review("id-1", clips, blank=False)
    assert page.count("</script>") == 1                    # وسم الإغلاق الحقيقي وحده
    assert "<img src=x" not in page and "a<b>.mkv" not in page
    data = json.loads(page.split("const DATA = ", 1)[1].split(";\n", 1)[0])
    assert data["clips"][0]["draft"].startswith("</script>")
    assert data["clips"][0]["where"] == "a<b>.mkv @ 0:01:05"
    assert "`" not in page.split("<script>", 1)[1]         # لا قوالب نصّية في السكربت


# ---------------------------------------------------------------------
# score
# ---------------------------------------------------------------------
def _manifest(hyps, blank=False):
    return {"eval_id": "e1", "model": "m", "blank": blank, "clips": [
        {"name": f"clip_{i:02d}", "source": "c.mkv" if i % 2 else "d.mkv",
         "start": 0.0, "duration": 30.0, "hyp": hyp}
        for i, hyp in enumerate(hyps, start=1)]}


def _refs(texts, **flags):
    clips = {}
    for i, text in enumerate(texts, start=1):
        name = f"clip_{i:02d}"
        clips[name] = {"text": text, "reviewed": True, "exclude": False,
                       **flags.get(name, {})}
    return {"eval_id": "e1", "clips": clips}


def test_score_pools_errors_over_words_not_over_clips():
    hyps = ["واحد اثنان ثلاثة اربعة", "خمسة"]
    refs = ["واحد اثنان ثلاثة أربعة", "ستة"]               # الهمزة لا تُحسب خطأ
    report = score(_manifest(hyps), _refs(refs))
    assert report["reference_words"] == 5
    assert report["wer"] == pytest.approx(1 / 5)
    assert report["word_accuracy"] == pytest.approx(0.8)
    assert report["clips_scored"] == 2 and report["untouched_clips"] == 1
    assert set(report["by_source"]) == {"c.mkv", "d.mkv"}
    assert report["worst_clips"][0]["name"] == "clip_02"


def test_excluded_and_unreviewed_clips_do_not_count():
    hyps = ["كلمة صحيحة", "ضجيج", "لم يراجع"]
    refs = _refs(["كلمة صحيحة", "", "لم يراجع"],
                 clip_02={"exclude": True}, clip_03={"reviewed": False})
    report = score(_manifest(hyps), refs)
    assert (report["clips_scored"], report["clips_excluded"],
            report["clips_unreviewed"]) == (1, 1, 1)
    assert report["wer"] == 0.0


def test_confidence_interval_brackets_the_estimate():
    hyps = [f"كلمة {'خطأ' if i % 3 == 0 else 'صحيحة'} اخرى ثابتة" for i in range(12)]
    refs = ["كلمة صحيحة اخرى ثابتة"] * 12
    report = score(_manifest(hyps), _refs(refs))
    low, high = report["wer_ci95"]
    assert low <= report["wer"] <= high and low < high
    acc_low, acc_high = report["word_accuracy_ci95"]
    assert acc_low == pytest.approx(1 - high) and acc_high == pytest.approx(1 - low)


def test_report_states_its_own_limits():
    few = score(_manifest(["نص قصير"]), _refs(["نص قصير"]))
    assert any("متفائل" in caveat for caveat in few["caveats"])       # مرجع من مسودّة
    assert any("صغيرة" in caveat for caveat in few["caveats"])        # عيّنة صغيرة
    blank = score(_manifest(["نص قصير"], blank=True), _refs(["نص قصير"]))
    assert not any("متفائل" in caveat for caveat in blank["caveats"])
    same = ["نفس النص تماما"] * 6
    lazy = score(_manifest(same), _refs(same))
    assert any("80%" in caveat for caveat in lazy["caveats"])


def test_score_refuses_references_from_another_eval_set():
    refs = _refs(["نص"])
    refs["eval_id"] = "other"
    with pytest.raises(SystemExit):
        score(_manifest(["نص"]), refs)


def test_score_needs_at_least_one_reviewed_clip():
    refs = _refs(["نص"], clip_01={"reviewed": False})
    with pytest.raises(SystemExit):
        score(_manifest(["نص"]), refs)


def test_another_model_is_scored_against_the_same_references():
    report = score(_manifest(["المسودة صحيحة"]), _refs(["المسودة صحيحة"]),
                   hypotheses={"clip_01": "المسودة خاطئة"})
    assert report["wer"] == pytest.approx(0.5)


def test_run_score_reads_downloaded_references_and_writes_a_report(tmp_path, monkeypatch):
    # مجلد «التنزيلات» الحقيقي قد يحمل مراجع قياسٍ آخر على جهاز المطوّر.
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "no-home"))
    (tmp_path / "eval_set.json").write_text(
        json.dumps(_manifest(["مرحبا بكم في الحصة"]), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "references (1).json").write_text(
        json.dumps(_refs(["مرحبا بكم في الحصة"]), ensure_ascii=False), encoding="utf-8")
    lines = []
    report = eval_real.run_score(tmp_path, log=lines.append)
    assert report["word_accuracy"] == 1.0
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["wer"] == 0.0
    assert any("دقة الكلمات" in line for line in lines)


def test_clip_is_a_plain_record():
    assert Clip("clip_01", "a.mkv", 1.0, 30.0).duration == 30.0
