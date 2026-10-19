"""لوحة الإحصاءات وإعداد مشاركتها (بلا شاشة)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from config.settings import AppConfig  # noqa: E402
from core import stats  # noqa: E402


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    from ui.main_window import MainWindow
    config = AppConfig()
    config.application.output_dir = tmp_path / "out"
    win = MainWindow(config, tmp_path / "config.json")
    win.dashboard.wait()
    app.processEvents()
    yield win
    win.dashboard.wait()
    win.close()


def _facts():
    return [stats.JobFact("1", "2026-10-02T10:00:00", "completed", "أ", 3600, 1800, 82,
                          outputs=[".docx"]),
            stats.JobFact("2", "2026-10-03T10:00:00", "failed", "<b>ب</b>", 60, 5)]


def test_empty_state_before_any_job(window):
    assert window.dashboard.empty.isVisibleTo(window.dashboard)
    assert not window.dashboard.content.isVisibleTo(window.dashboard)


def test_tiles_and_charts_show_the_numbers(window):
    board = window.dashboard
    board.show_stats(stats.compute(_facts()))
    assert board.content.isVisibleTo(board)
    assert board.tile_documents.value.text() == "1"
    assert board.tile_quality.value.text() == "82"
    assert board.tile_success.value.text() == "50٪"
    assert board.tile_speed.value.text() == "2.0×"
    # المحور تاريخ المستند لا رقمٌ ترتيبي (1.15.1)
    assert board.quality_chart.items == [("2 أكتوبر", 82.0)]
    assert "&lt;b&gt;" in board.recent.text()       # الاسم مُهرَّب
    assert "82" in board.quality_chart.accessible_summary()


def test_running_job_and_shared_denominator(window):
    """1.15.1: الجارية «قيد المعالجة»، و«من N منتهية» = مقام نسبة النجاح."""
    board = window.dashboard
    facts = _facts() + [stats.JobFact("3", "2026-10-04T10:00:00", "running", "ج", 2400)]
    board.show_stats(stats.compute(facts, busy=True))
    assert "قيد المعالجة" in board.recent.text()
    assert "لم تكتمل" not in board.recent.text() + board.tile_success.note.text()
    assert board.tile_documents.note.text().startswith("من مهمّتين")
    board.show_stats(stats.compute(facts, busy=False))
    assert "توقّفت" in board.recent.text()


def test_failed_job_shows_no_quality_score(window):
    board = window.dashboard
    facts = [stats.JobFact("9", "2026-10-15T10:00:00", "failed", "د", 2400,
                           quality_score=72, outputs=[".docx"])]
    board.show_stats(stats.compute(facts))
    assert "جودة 72" not in board.recent.text()
    assert "أُنشئ قبل الفشل" in board.recent.text()


def test_logo_box_offers_the_choices(window, tmp_path, monkeypatch):
    from PIL import Image

    from document import branding

    assert window.logo_combo.count() == len(branding.CHOICES)
    window.logo_combo.setCurrentIndex(window.logo_combo.findData("none"))
    assert window.config.document.logo_choice == "none"
    assert window.logo_preview.text() == "بلا شعار"
    source = tmp_path / "school.png"
    Image.new("RGB", (400, 160), (10, 80, 160)).save(source)
    monkeypatch.setattr(branding, "branding_dir", lambda: tmp_path / "branding")
    from PyQt6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(source), "")))
    assert window._choose_logo()
    assert window.config.document.logo_choice == "custom"
    assert window.config.document.logo_path.parent == tmp_path / "branding"
    assert window.logo_combo.currentData() == "custom"


def test_header_promise_matches_cloud_rewrite(window):
    window.config.rewrite.enabled = False
    window._refresh_privacy_header()
    assert window.privacy_header.text() == "المعالجة على جهازك بالكامل"
    window.config.rewrite.enabled = True
    window.config.rewrite.provider = "anthropic"
    window.config.rewrite.send_images = True
    window._refresh_privacy_header()
    assert "مزوّد سحابي" in window.privacy_header.text()
    assert "محلي بالكامل" not in window.windowTitle()


def test_refresh_collects_from_disk(app, window, tmp_path):
    stats.record_job(window.config, tmp_path / "out" / "x", "failed", 3.0)
    window.dashboard.refresh()
    window.dashboard.wait()
    for _ in range(10):
        app.processEvents()
    assert window.dashboard.stats.failed == 1


def test_share_stats_toggle_saves_config(window):
    window.share_stats_check.setChecked(False)
    assert window.config.application.share_usage_stats is False
    window.share_stats_check.setChecked(True)
    assert window.config.application.share_usage_stats is True


def test_share_stats_locked_by_policy(app, tmp_path):
    from ui.main_window import MainWindow
    config = AppConfig()
    config.application.share_usage_stats = False
    config.policy_locked = {"allow_usage_stats": "مفروض بسياسة الجهاز"}
    win = MainWindow(config, tmp_path / "c.json")
    win.dashboard.wait()
    assert not win.share_stats_check.isEnabled()
    assert not win.share_stats_check.isChecked()
    win.close()
