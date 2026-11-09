"""1.22.0: تغيير كود التفعيل والبرنامج مفعّل — بلا انتظار انتهاء التجربة.

الكود الجديد يحلّ محلّ القديم فورًا، ومدّته من لحظة تفعيله، والواجهة تلتزم
بباقته: «معين المُدار» يظهر لكود مُدار ويختفي لغيره.
"""
import os
import time

import pytest

from licensing import app_gate, client
from licensing.lease import Lease
from licensing.signing import generate_keypair, sign
from licensing.state import StateStore

DAY = 86400


@pytest.fixture
def licensed(tmp_path, monkeypatch):
    private, public = generate_keypair()
    monkeypatch.setattr("licensing.keys.PUBLIC_KEY_B64", public)
    monkeypatch.setattr(app_gate, "_device", "dev-1")
    monkeypatch.setattr(app_gate, "_store", StateStore(
        "dev-1", [tmp_path / "a.json", tmp_path / "b.json"], use_registry=False))
    activated = []

    def issue(code, days, tier):
        now = time.time()
        return sign(Lease(code=code, device="dev-1", issued_at=int(now),
                          expires_at=int(now + days * DAY), max_days=days,
                          recheck_at=int(now + 30 * DAY), grace_days=3,
                          plan={"trial": "تجربة · أسبوع", "managed": "مُدار · 6 أشهر",
                                "solo": "مفتاحك · 6 أشهر"}[tier],
                          extra={"tier": tier}), private)

    codes = {"VTAW-TRIAL": (7, "trial"), "VTAW-MANAGED": (182, "managed"),
             "VTAW-SOLO": (182, "solo"), "VTAW-SHORT": (3, "solo")}

    def activate(code, device, app_version=""):
        activated.append(code)
        return issue(code, *codes[code])

    monkeypatch.setattr(client, "activate", activate)
    app_gate.activate("VTAW-TRIAL")
    assert app_gate.is_trial()
    return activated


def test_new_code_replaces_the_active_trial_immediately(licensed):
    app_gate.install_lease(app_gate.fetch_lease("VTAW-MANAGED"))
    assert app_gate.current_code() == "VTAW-MANAGED"
    assert app_gate.managed_allowed() and not app_gate.is_trial()
    lease = app_gate.current_lease()
    assert lease.expires_at - time.time() > 180 * DAY          # من لحظة التفعيل


# ── الواجهة ──────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PyQt6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def _window(tmp_path, monkeypatch):
    from config.settings import AppConfig
    from ui.main_window import MainWindow

    # يسأل الخادم عن الرصيد في خيط — لا شبكة في الاختبار
    monkeypatch.setattr(MainWindow, "_auto_pick_managed", lambda self: None)
    window = MainWindow(AppConfig(), tmp_path / "c.yaml")
    window.license_verdict = app_gate.check()
    return window


def _enter(monkeypatch, code, confirm=None):
    """يحاكي المستخدم: يكتب الكود ويضغط «تفعيل» داخل الحوار."""
    from PyQt6.QtWidgets import QMessageBox

    from ui.license_dialog import LicenseDialog

    asked = []
    if confirm is not None:
        def question(*a, **k):
            asked.append(a[2])
            return confirm
        monkeypatch.setattr(QMessageBox, "question", question)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    def run(dialog):
        dialog.code_input.setText(code)
        dialog._activate()
        return 0

    monkeypatch.setattr(LicenseDialog, "exec", run)
    return asked


def _providers(window):
    return [window.provider_combo.itemData(i) for i in range(window.provider_combo.count())]


def test_ui_follows_the_new_codes_tier(qapp, licensed, monkeypatch, tmp_path):
    window = _window(tmp_path, monkeypatch)
    assert "maeen_managed" not in _providers(window)          # تجربة

    _enter(monkeypatch, "VTAW-MANAGED")
    window.change_license_code()
    assert "maeen_managed" in _providers(window)              # مُدار ⇒ يظهر
    assert "الترخيص: حتى" in window.license_badge.text()

    _enter(monkeypatch, "VTAW-SOLO")
    window.change_license_code()
    assert "maeen_managed" not in _providers(window)          # غير مُدار ⇒ يختفي
    assert app_gate.current_code() == "VTAW-SOLO"
    window.close()


def test_shorter_code_needs_confirmation(qapp, licensed, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QMessageBox

    app_gate.install_lease(app_gate.fetch_lease("VTAW-SOLO"))
    window = _window(tmp_path, monkeypatch)
    asked = _enter(monkeypatch, "VTAW-SHORT", confirm=QMessageBox.StandardButton.No)
    window.change_license_code()
    assert asked and app_gate.current_code() == "VTAW-SOLO"   # رُفض ⇒ بقي الحالي

    _enter(monkeypatch, "VTAW-SHORT", confirm=QMessageBox.StandardButton.Yes)
    window.change_license_code()
    assert app_gate.current_code() == "VTAW-SHORT"
    window.close()


def test_same_code_changes_nothing(qapp, licensed, monkeypatch, tmp_path):
    from ui.license_dialog import LicenseDialog

    dialog = LicenseDialog(app_gate.check(), "", None, replacing=True)
    assert "…RIAL" in dialog.status.text()
    dialog.code_input.setText("VTAW-TRIAL")
    dialog._activate()
    assert "المفعّل حاليًا" in dialog.status.text()
