"""ما يحدث عند الإغلاق، وما يُقال عن إعدادٍ تالف.

كلاهما عطبٌ لا يظهر في أي مقياس: مسارٌ يمرّ بلا خطأ في السجلّ، ويكلّف
المستخدم ساعةً من عمله أو كل خياراته.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# انظر تعليق ``tests/test_ui_layout.py``: الحارس على الوحدة الفعلية،
# فـ``import PyQt6`` ينجح بلا أي مكتبة نظام.
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from config.settings import AppConfig  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _Event:
    """بديل ``QCloseEvent`` يسجّل ما فُعل به."""

    def __init__(self):
        self.accepted = None

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


class _Thread:
    def __init__(self, running):
        self._running = running

    def isRunning(self):
        return self._running

    def quit(self):
        self._running = False

    def wait(self, _ms):
        return True


def _window(app, config=None):
    from ui.main_window import MainWindow
    return MainWindow(config or AppConfig())


# ── إشعار الإعداد التالف ──────────────────────────────────────────────

def test_corrupt_config_is_announced_in_the_gui(app, monkeypatch):
    """كان سطر الأوامر وحده يعرضه، والمعلّم لا يستعمل سطر الأوامر."""
    shown = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *args, **kw: shown.append(args[2]))

    config = AppConfig()
    config.load_error = "تعذّرت قراءة ملف الإعداد settings.yaml — استُخدمت القيم الافتراضية."
    window = _window(app, config)

    assert shown, "الإعداد التالف مرّ بصمت في الواجهة"
    assert "settings.yaml" in shown[0]
    window.close()


def test_a_healthy_config_says_nothing(app, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *args, **kw: shown.append(args))
    window = _window(app)
    assert not shown
    window.close()


# ── تأكيد الإغلاق أثناء المعالجة ──────────────────────────────────────

def test_closing_mid_run_asks_first(app, monkeypatch):
    """محاضرة ثلاث ساعات كانت تُلغى بنقرة على «×» بلا سؤال."""
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kw: (asked.append(args[2]),
                             QMessageBox.StandardButton.No)[1])

    window = _window(app)
    window.thread = _Thread(running=True)
    event = _Event()
    window.closeEvent(event)

    assert asked, "أُغلقت النافذة أثناء المعالجة بلا سؤال"
    assert event.accepted is False, "النافذة أُغلقت رغم رفض المستخدم"
    window.thread = None


def test_the_question_says_the_work_is_not_lost(app, monkeypatch):
    """السؤال يخبر أن المراحل محفوظة — لا يخوّف فحسب."""
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kw: (asked.append(args[2]),
                             QMessageBox.StandardButton.No)[1])
    window = _window(app)
    window.thread = _Thread(running=True)
    window.closeEvent(_Event())

    assert "تُكمل من حيث توقّفت" in asked[0]
    window.thread = None


def test_confirming_closes_and_cancels(app, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *args, **kw: QMessageBox.StandardButton.Yes)

    class _Token:
        cancelled = False

        def cancel(self):
            self.cancelled = True

    window = _window(app)
    window.thread = _Thread(running=True)
    window.cancel_token = _Token()
    event = _Event()
    window.closeEvent(event)

    assert event.accepted is True
    assert window.cancel_token.cancelled, "أُغلقت النافذة بلا إلغاء تعاوني"
    window.thread = None


def test_closing_while_idle_asks_nothing(app, monkeypatch):
    """لا سؤال بلا سبب: نافذة خاملة تُغلق فورًا."""
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *args, **kw: asked.append(args))
    window = _window(app)
    event = _Event()
    window.closeEvent(event)

    assert not asked
    assert event.accepted is True
