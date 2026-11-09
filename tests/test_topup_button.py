"""1.22.1: «الرصيد لا يكفي» يعرض زرّ «شحن الرصيد» إلى الصفحة الصحيحة."""
import os

import pytest

from ai.providers import ProviderBillingError, billing_url
from config.settings import AppConfig
from utils.error_reporting import format_error_for_user


def test_billing_page_follows_the_chosen_provider():
    rewrite = AppConfig().rewrite
    rewrite.provider = "anthropic"
    assert "console.anthropic.com" in billing_url(rewrite)
    rewrite.provider, rewrite.preset = "openai_compatible", "openai"
    assert "platform.openai.com" in billing_url(rewrite)
    rewrite.preset = "custom"
    assert billing_url(rewrite) == ""                 # خادم الجهة: لا صفحة معروفة


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PyQt6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def _window(tmp_path):
    from ui.main_window import MainWindow
    return MainWindow(AppConfig(), tmp_path / "c.yaml")


def _click(monkeypatch, button_text):
    from PyQt6.QtWidgets import QMessageBox
    shown = {}

    def exec_(box):
        shown["buttons"] = [b.text() for b in box.buttons()]
        target = next((b for b in box.buttons() if b.text() == button_text), None)
        monkeypatch.setattr(box, "clickedButton", lambda: target)
        return 0

    monkeypatch.setattr(QMessageBox, "exec", exec_)
    return shown


def test_provider_billing_error_opens_its_billing_page(qapp, monkeypatch, tmp_path):
    from PyQt6.QtGui import QDesktopServices

    window = _window(tmp_path)
    window.config.rewrite.provider = "anthropic"
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    shown = _click(monkeypatch, "شحن الرصيد")
    window.on_failed(format_error_for_user(ProviderBillingError("نفد رصيد حساب Anthropic")))
    assert "شحن الرصيد" in shown["buttons"]
    assert opened == ["https://console.anthropic.com/settings/billing"]
    window.close()


def test_managed_without_topup_page_copies_a_request(qapp, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from ai.providers import CreditExhaustedError
    from licensing import app_gate

    monkeypatch.setattr("licensing.keys.TOPUP_URL", "")
    monkeypatch.setattr(app_gate, "current_code", lambda: "VTAW-M")
    monkeypatch.setattr(app_gate, "device_id", lambda: "DEV")
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    window = _window(tmp_path)
    _click(monkeypatch, "نسخ طلب الشحن")
    window.on_failed(format_error_for_user(CreditExhaustedError()))
    copied = QApplication.clipboard().text()
    assert "VTAW-M" in copied and "DEV" in copied
    window.close()


def test_other_errors_keep_the_plain_dialog(qapp, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QMessageBox

    seen = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: seen.append(a[2]))
    window = _window(tmp_path)
    window.on_failed("[E4001] فشل التفريغ")
    assert seen == ["[E4001] فشل التفريغ"]
    window.close()
