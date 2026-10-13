"""إحصاءات الاستخدام: السجلّ، والتجميع، وما يُرسل، وشروط الإرسال."""
import json
from datetime import datetime
from pathlib import Path

import pytest

from config import policy as policy_module
from config.settings import AppConfig
from core import stats
from utils import stats_report


def _job(output: Path, name: str, status="completed", minutes=30, score=80.0,
         updated="2026-10-10T10:00:00", figures=2) -> Path:
    job_dir = output / name
    job_dir.mkdir(parents=True)
    (job_dir / "job_state.json").write_text(json.dumps({
        "status": status, "progress": 100.0, "updated_at": updated,
        "video_path": str(output / f"{name}.mp4")}), encoding="utf-8")
    (job_dir / "metadata.json").write_text(json.dumps(
        {"duration_seconds": minutes * 60}), encoding="utf-8")
    blocks = [{"kind": "figure"}] * figures + [{"kind": "paragraph"}]
    (job_dir / "plan.json").write_text(json.dumps({
        "quality_score": score, "sections": [{"blocks": blocks}]}), encoding="utf-8")
    (job_dir / f"{name}.docx").write_bytes(b"x")
    return job_dir


def test_record_job_appends_one_line_with_config_features(tmp_path):
    job_dir = _job(tmp_path / "out", "محاضرة 1")
    config = AppConfig()
    config.rewrite.enabled = True
    config.rewrite.send_images = True
    stats.record_job(config, job_dir, "completed", 600.0, directory=tmp_path / "s")
    lines = (tmp_path / "s" / stats.LEDGER_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    fact = json.loads(lines[0])
    assert fact["status"] == "completed" and fact["duration_seconds"] == 1800
    assert fact["quality_score"] == 80.0 and fact["figures"] == 2
    assert fact["rewrite"] is True and fact["vision"] is True
    assert ".docx" in fact["outputs"]


def test_record_job_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    stats.record_job(AppConfig(), tmp_path / "missing", "failed", 1.0,
                     directory=blocker / "sub")   # مجلد لا يُنشأ


def test_ledger_wins_and_old_jobs_are_backfilled_from_output_dir(tmp_path):
    out = tmp_path / "out"
    first = _job(out, "أ", score=60)
    _job(out, "ب", score=90, updated="2026-09-01T10:00:00")
    ledger = tmp_path / "s"
    stats.record_job(AppConfig(), first, "failed", 10.0, directory=ledger)
    stats.record_job(AppConfig(), first, "completed", 20.0, directory=ledger)
    facts = stats.load_facts([out], directory=ledger)
    assert len(facts) == 2                       # لا تكرار للمهمّة
    by_name = {f.name: f for f in facts}
    assert by_name["أ"].status == "completed"    # الأحدث في السجلّ
    assert by_name["ب"].quality_score == 90       # مُستدرَكة من المجلد


def test_compute_aggregates(tmp_path):
    now = datetime(2026, 10, 13)
    facts = [
        stats.JobFact("1", "2026-10-02T10:00:00", "completed", "أ", 3600, 1800, 80,
                      sections=3, figures=4, questions=5, outputs=[".docx", ".html"],
                      rewrite=True),
        stats.JobFact("2", "2026-09-02T10:00:00", "completed", "ب", 1800, 900, 60,
                      outputs=[".docx"]),
        stats.JobFact("3", "2026-10-05T10:00:00", "failed", "ج", 600, 30),
        stats.JobFact("4", "2026-10-06T10:00:00", "cancelled", "د"),
    ]
    rec = tmp_path / "rec"
    rec.mkdir()
    (rec / "x.recording.json").write_text(json.dumps({"duration_seconds": 120}))
    result = stats.compute(facts, rec, now)
    assert (result.jobs_total, result.completed, result.failed, result.cancelled) == (4, 2, 1, 1)
    assert result.success_rate == pytest.approx(200 / 3)
    assert result.video_hours == pytest.approx(1.5)
    assert result.speed_ratio == pytest.approx(2.0)
    assert result.average_quality == pytest.approx(70)
    assert result.formats[".docx"] == 2 and result.features["rewrite"] == 1
    assert result.this_month_jobs == 1 and result.this_month_minutes == pytest.approx(60)
    assert [b.month for b in result.months][-1] == "2026-10"
    assert [b.jobs for b in result.months][-2:] == [1, 1]   # المكتملة وحدها
    assert (result.recordings, result.recording_seconds) == (1, 120)
    assert result.recent[0].name == "د"


def test_payload_carries_counts_only():
    facts = [stats.JobFact("1", "2026-10-02T10:00:00", "completed",
                           "سرّي جدًا", 600, 100, 75, outputs=[".docx"])]
    payload = stats.to_payload(stats.compute(facts))
    text = json.dumps(payload, ensure_ascii=False)
    assert "سرّي" not in text and "name" not in payload
    assert payload["completed"] == 1 and payload["video_minutes"] == 10
    assert payload["last_job_day"] == "2026-10-02"


# ── الإرسال ───────────────────────────────────────────────────────────
@pytest.fixture
def licensed(monkeypatch):
    from licensing import app_gate
    monkeypatch.delenv("VTAD_DISABLE_USAGE_REPORT", raising=False)
    monkeypatch.setattr(app_gate, "licensing_enabled", lambda: True)
    monkeypatch.setattr(app_gate, "current_code", lambda: "VTAW1-ABC")
    monkeypatch.setattr(app_gate, "device_id", lambda: "device-1")


def test_report_sends_payload_and_throttles(licensed, tmp_path):
    config = AppConfig()
    config.application.output_dir = tmp_path / "out"
    sent = []
    sender = lambda *args: sent.append(args)  # noqa: E731
    assert stats_report.maybe_report(config, now=1000.0, sender=sender) is True
    code, device, payload, _version = sent[0]
    assert (code, device) == ("VTAW1-ABC", "device-1")
    assert "jobs_total" in payload
    assert stats_report.maybe_report(config, now=2000.0, sender=sender) is False
    assert stats_report.maybe_report(config, now=2000.0, sender=sender, force=True)
    assert len(sent) == 2


def test_report_respects_setting_and_policy(licensed):
    config = AppConfig()
    sender = lambda *a: pytest.fail("ما كان يجب أن يُرسل")  # noqa: E731
    policy_module.apply(config, policy_module.Policy(
        allow_usage_stats=False, locked={"allow_usage_stats": "سياسة"}))
    assert config.application.share_usage_stats is False
    assert stats_report.maybe_report(config, force=True, sender=sender) is False


def test_report_failure_is_silent(licensed):
    def broken(*_):
        raise ConnectionError("offline")
    assert stats_report.maybe_report(AppConfig(), force=True, sender=broken) is False


def test_no_report_without_activation(monkeypatch):
    from licensing import app_gate
    monkeypatch.delenv("VTAD_DISABLE_USAGE_REPORT", raising=False)
    monkeypatch.setattr(app_gate, "licensing_enabled", lambda: True)
    monkeypatch.setattr(app_gate, "current_code", lambda: None)
    assert stats_report.maybe_report(
        AppConfig(), force=True, sender=lambda *a: pytest.fail("أُرسل")) is False
