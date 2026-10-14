"""زر «تسجيل اجتماع» ونافذته: حالات العرض والحراسة (بلا OBS ولا شاشة)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# كما في test_ui_layout: الحارس على الوحدة الفعلية لا على الحزمة الجذر.
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from config.settings import AppConfig  # noqa: E402
from recording.session import MeetingRecorder, RecorderState  # noqa: E402


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def exe(tmp_path):
    path = tmp_path / "obs" / "bin" / "64bit" / "obs64.exe"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    return path


def _dialog(config, tmp_path):
    from ui.recording_dialog import RecordingDialog

    recorder = MeetingRecorder(config, root=tmp_path / "install")
    return RecordingDialog(config, recorder=recorder), recorder


def test_main_window_has_record_button(app):
    from ui.main_window import MainWindow

    window = MainWindow(AppConfig())
    assert window.record_button.isEnabled()
    window.close()


def test_main_window_button_disabled_by_policy(app):
    from ui.main_window import MainWindow

    config = AppConfig()
    config.recording.enabled = False
    config.policy_locked = {"allow_meeting_recording": "سياسة المدرسة"}
    window = MainWindow(config)
    assert not window.record_button.isEnabled()
    assert "سياسة المدرسة" in window.record_button.toolTip()
    window.close()


def test_recorded_file_becomes_the_source(app, tmp_path):
    from ui.main_window import MainWindow

    window = MainWindow(AppConfig())
    recording = tmp_path / "Maeen-Recording_x.mkv"
    window._set_source(recording, audio_only=False)
    assert window.video_path == recording and not window.audio_only
    assert window.start_button.isEnabled()
    window.close()


def test_dialog_offers_install_when_tool_missing(app, tmp_path):
    dialog, _ = _dialog(AppConfig(), tmp_path)
    assert not dialog.install_button.isHidden()
    assert dialog.start_button.isHidden() and dialog.consent_check.isHidden()
    assert "OBS" in dialog.info.text()


def test_dialog_hides_install_when_download_banned(app, tmp_path):
    config = AppConfig()
    config.recording.allow_download = False
    dialog, _ = _dialog(config, tmp_path)
    assert dialog.install_button.isHidden()
    assert "سياسة الجهاز" in dialog.info.text()


def test_dialog_blocks_everything_when_recording_disabled(app, tmp_path):
    config = AppConfig()
    config.recording.enabled = False
    dialog, _ = _dialog(config, tmp_path)
    assert all(w.isHidden() for w in (
        dialog.install_button, dialog.start_button, dialog.stop_button))
    assert "معطّل" in dialog.info.text()


def test_start_needs_consent_checkbox(app, tmp_path, exe):
    config = AppConfig()
    config.recording.obs_path = str(exe)
    dialog, _ = _dialog(config, tmp_path)
    assert not dialog.start_button.isHidden()
    assert not dialog.start_button.isEnabled()
    dialog.consent_check.setChecked(True)
    assert dialog.start_button.isEnabled()
    dialog.consent_check.setChecked(False)
    assert not dialog.start_button.isEnabled()


def test_cannot_close_while_recording(app, tmp_path, exe, monkeypatch):
    config = AppConfig()
    config.recording.obs_path = str(exe)
    dialog, recorder = _dialog(config, tmp_path)
    shown = []
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))

    recorder.state = RecorderState.RECORDING
    dialog._refresh()
    assert not dialog.stop_button.isHidden() and dialog.stop_button.isEnabled()
    assert not dialog.close_button.isEnabled()
    assert dialog.start_button.isHidden()

    dialog.reject()
    assert shown and "أوقف التسجيل" in shown[0]
    assert dialog.result() == 0           # لم يُغلق

    recorder.state = RecorderState.IDLE   # تنظيف


def test_dialog_survives_real_event_dispatch(app, tmp_path):
    """انحدار: إشارة اسمها ``event`` كانت تُسقط النافذة عند أول حدث حقيقي.

    بناء النافذة وحده لا يمرّ بـ ``QObject.event``؛ العرض ومعالجة الأحداث
    هما اللذان يستدعيانه.
    """
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QMouseEvent  # noqa: F401

    dialog, _ = _dialog(AppConfig(), tmp_path)
    assert callable(dialog.event)                       # دالة Qt لا إشارة
    dialog.show()
    app.processEvents()
    dialog.log_event.emit("رسالة تجريبية")
    app.processEvents()
    assert dialog.status.text() == "رسالة تجريبية"
    assert dialog.event(QEvent(QEvent.Type.User)) in (True, False)
    dialog.close()
