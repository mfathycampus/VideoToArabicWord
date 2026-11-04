"""1.19.4: التقدير المسبق بسرعة هذا الجهاز، ووسمه أثناء المعالجة."""
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from config.settings import AppConfig  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _window(monkeypatch, tmp_path, ledger_rows):
    monkeypatch.setenv("VTAD_STATS_DIR", str(tmp_path))
    with open(tmp_path / "jobs.jsonl", "w", encoding="utf-8") as handle:
        for row in ledger_rows:
            handle.write(json.dumps(row) + "\n")
    from ui.main_window import MainWindow
    window = MainWindow(AppConfig())
    window.video_path = Path("lecture.mp4")
    monkeypatch.setattr(window, "_source_duration_seconds", lambda: 72 * 60.0)
    monkeypatch.setattr("ui.main_window.GPUManager.is_cuda_available",
                        staticmethod(lambda: False))
    return window


def _row(window, elapsed, key="k"):
    return {"key": key, "at": "2026-11-01T10:00:00", "status": "completed",
            "duration_seconds": 3600.0, "elapsed_seconds": elapsed,
            "engine": window.engine_combo.currentData() or "faster-whisper",
            "model": window.model_combo.currentData()}


def test_estimate_uses_this_devices_speed(app, monkeypatch, tmp_path):
    probe = _window(monkeypatch, tmp_path, [])
    rows = [_row(probe, 9000.0, f"k{i}") for i in range(3)]         # الجهاز: 2.5 ثانية لكل ثانية
    window = _window(monkeypatch, tmp_path, rows)
    window._refresh_estimate()
    text = window.estimate_label.text()
    assert "3 ساعات" in text and "بسرعة هذا الجهاز في آخر 3 مهامّ" in text


def test_estimate_falls_back_to_generic_factor(app, monkeypatch, tmp_path):
    window = _window(monkeypatch, tmp_path, [])
    window._refresh_estimate()
    assert "تقدير عام" in window.estimate_label.text()


def test_estimate_is_labelled_while_running_and_restored(app, monkeypatch, tmp_path):
    window = _window(monkeypatch, tmp_path, [])
    window._refresh_estimate()
    before = window.estimate_label.text()
    window.estimate_label.setText(f"التقدير قبل البدء: {before}")   # كما في start()
    window._reset_buttons()
    assert window.estimate_label.text() == before
