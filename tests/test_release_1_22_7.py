"""1.22.7: بصمة الجهاز لا تتغيّر بإعادة تسميته — والانتقال إليها بلا تصفير.

اسم الجهاز واسم المستخدم كانا في البصمة، فإعادة التسمية أو حساب ويندوز آخر
كانت تُسقط الترخيص على الجهاز نفسه. اختبارات الخادم المقابلة في
``server/test/worker.test.mjs`` (قسم 1.22.7).
"""
from __future__ import annotations

import time

import pytest

from licensing import app_gate, client, fingerprint
from licensing.gate import Status
from licensing.lease import Lease, LeaseError
from licensing.signing import generate_keypair, sign
from licensing.state import StateStore

DAY = 86400
OLD, NEW = "0123456789abcdef0123", "aaaabbbbccccdddd0000"


# ---------------------------------------------------------------------
# البصمة
# ---------------------------------------------------------------------
@pytest.fixture
def machine(monkeypatch):
    values = {"guid": "GUID-1", "node": "PC-A", "user": "teacher"}
    monkeypatch.setattr(fingerprint, "_hardware_ids", lambda: [values["guid"], "", "", ""])
    monkeypatch.setattr(fingerprint.platform, "node", lambda: values["node"])
    monkeypatch.setattr(fingerprint.platform, "machine", lambda: "AMD64")
    monkeypatch.setenv("USERNAME", values["user"])
    monkeypatch.delenv("USER", raising=False)
    return values, monkeypatch


def test_renaming_the_pc_or_switching_user_keeps_the_fingerprint(machine):
    values, monkeypatch = machine
    before, legacy_before = fingerprint.device_fingerprint(), fingerprint.legacy_fingerprint()
    values["node"] = "PC-RENAMED"
    monkeypatch.setenv("USERNAME", "another-account")
    assert fingerprint.device_fingerprint() == before
    assert fingerprint.legacy_fingerprint() != legacy_before       # هذا ما كان يُسقطه
    assert len(before) == 20 and int(before, 16) >= 0


def test_another_machine_has_another_fingerprint(machine):
    values, _ = machine
    first = fingerprint.device_fingerprint()
    values["guid"] = "GUID-2"
    assert fingerprint.device_fingerprint() != first


def test_without_hardware_ids_the_old_fingerprint_is_kept(monkeypatch):
    """بيئةٌ لا يُقرأ منها معرّف: بلا الاسمين لتشابهت بصمات كل الأجهزة مثلها."""
    monkeypatch.setattr(fingerprint, "_hardware_ids", lambda: ["", "", "", ""])
    assert fingerprint.device_fingerprint() == fingerprint.legacy_fingerprint()


# ---------------------------------------------------------------------
# الحالة المحلية المختومة بالبصمة القديمة
# ---------------------------------------------------------------------
def test_state_sealed_with_the_old_fingerprint_is_read_and_resealed(tmp_path):
    paths = [tmp_path / "a.json", tmp_path / "b.json"]
    StateStore(OLD, paths, use_registry=False).write({"lease": "L", "saved_at": 5})
    upgraded = StateStore(NEW, paths, use_registry=False, legacy_devices=[OLD])
    assert upgraded.read()["lease"] == "L"
    upgraded.write({**upgraded.read(), "saved_at": 6})
    assert StateStore(NEW, paths, use_registry=False).read()["saved_at"] == 6
    assert StateStore(OLD, paths, use_registry=False).read() == {}   # خُتمت بالجديدة


def test_state_from_another_machine_is_still_rejected(tmp_path):
    paths = [tmp_path / "a.json"]
    StateStore("someone-else", paths, use_registry=False).write({"lease": "L"})
    assert StateStore(NEW, paths, use_registry=False, legacy_devices=[OLD]).read() == {}


# ---------------------------------------------------------------------
# الانتقال عبر الخادم
# ---------------------------------------------------------------------
@pytest.fixture
def upgraded(tmp_path, monkeypatch):
    """جهازٌ عقده صدر لبصمته القديمة، وصار التطبيق يحسب الجديدة."""
    private, public = generate_keypair()
    monkeypatch.setattr("licensing.keys.PUBLIC_KEY_B64", public)
    monkeypatch.setattr(app_gate, "_device", NEW)
    monkeypatch.setattr(app_gate, "_legacy", (OLD,))
    paths = [tmp_path / "a.json", tmp_path / "b.json"]
    monkeypatch.setattr(app_gate, "_store", StateStore(
        NEW, paths, use_registry=False, legacy_devices=[OLD]))

    def issue(device, recheck_in=30 * DAY):
        now = int(time.time())
        return sign(Lease(code="VTAW-CODE", device=device, issued_at=now,
                          expires_at=now + 180 * DAY, max_days=180,
                          recheck_at=now + recheck_in, grace_days=3, plan="شهر"),
                    private)

    # العقد القديم كما ثبّته إصدارٌ سابق: مختومًا ببصمته القديمة
    StateStore(OLD, paths, use_registry=False).write(
        {"lease": issue(OLD), "saved_at": int(time.time())})
    calls = []
    return issue, calls, monkeypatch


def test_a_lease_for_the_old_fingerprint_keeps_working_after_upgrade(upgraded):
    verdict = app_gate.check()
    assert verdict.status is Status.ACTIVE
    assert app_gate.device_id() == OLD          # ما يعرفه الخادم حتى الانتقال
    assert app_gate.machine_ids() == (NEW, OLD)


def test_recheck_moves_the_lease_to_the_new_fingerprint(upgraded):
    issue, calls, monkeypatch = upgraded

    def recheck(code, device, app_version="", previous=""):
        calls.append((device, previous))
        return issue(device)

    monkeypatch.setattr(client, "recheck", recheck)
    verdict = app_gate.refresh_online()
    assert calls == [(NEW, OLD)]
    assert verdict.status is Status.ACTIVE
    assert app_gate.device_id() == NEW
    # التجديد التالي لا يرسل بصمة سابقة: الانتقال تمّ
    app_gate.refresh_online()
    assert calls[-1] == (NEW, "")


def test_an_old_server_that_refuses_the_move_renews_the_old_way(upgraded):
    issue, calls, monkeypatch = upgraded

    def recheck(code, device, app_version="", previous=""):
        calls.append((device, previous))
        if device == NEW:
            raise client.ActivationError("أُعيد ضبط أجهزة هذا الكود.")
        return issue(device)

    monkeypatch.setattr(client, "recheck", recheck)
    verdict = app_gate.refresh_online()
    assert calls == [(NEW, OLD), (OLD, "")]
    assert verdict.status is Status.ACTIVE and app_gate.device_id() == OLD


def test_offline_does_not_fall_back_or_lose_the_lease(upgraded):
    _, calls, monkeypatch = upgraded

    def recheck(code, device, app_version="", previous=""):
        calls.append(device)
        raise client.ActivationError("لا إنترنت", offline=True)

    monkeypatch.setattr(client, "recheck", recheck)
    assert app_gate.refresh_online().status is Status.ACTIVE
    assert calls == [NEW]


def test_entering_the_code_again_also_sends_the_old_fingerprint(upgraded):
    issue, calls, monkeypatch = upgraded

    def activate(code, device, app_version="", previous=""):
        calls.append((device, previous))
        return issue(device)

    monkeypatch.setattr(client, "activate", activate)
    app_gate.activate("VTAW-CODE")
    assert calls == [(NEW, OLD)] and app_gate.device_id() == NEW


def test_a_lease_for_another_machine_is_still_refused(upgraded):
    issue, _, _ = upgraded
    with pytest.raises(LeaseError):
        app_gate.install_lease(issue("ffffffffffffffffffff"))


def test_client_sends_previous_only_when_it_differs(monkeypatch):
    sent = []
    monkeypatch.setattr(client, "_post", lambda path, payload, **kw: sent.append(payload) or {"lease": "L"})
    client.recheck("vtaw-x", NEW, "1", previous=OLD)
    client.recheck("vtaw-x", NEW, "1", previous=NEW)
    client.activate("vtaw-x", NEW, "1")
    assert sent[0]["previous"] == OLD and sent[0]["code"] == "VTAW-X"
    assert "previous" not in sent[1] and "previous" not in sent[2]


# ---------------------------------------------------------------------
# «مهلة انتظار ردّ النموذج» من الواجهة
# ---------------------------------------------------------------------
@pytest.fixture
def window(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication

    from config.settings import AppConfig
    from ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow(AppConfig(), tmp_path / "config.yaml")
    yield win
    win.close()
    del app


def test_timeout_is_set_from_the_ui_for_rewrite_and_study_and_saved(window, tmp_path):
    from config.settings import AppConfig

    assert window.timeout_spin.value() == 180
    window.timeout_spin.setValue(600)
    assert window.config.rewrite.timeout_seconds == 600
    assert window.config.study.timeout_seconds == 600
    saved = AppConfig.load(tmp_path / "config.yaml")
    assert saved.rewrite.timeout_seconds == 600 and saved.study.timeout_seconds == 600


def test_timeout_is_bounded(window):
    window.timeout_spin.setValue(5)
    assert window.config.rewrite.timeout_seconds == window.TIMEOUT_RANGE[0]
    window.timeout_spin.setValue(10 ** 6)
    assert window.config.rewrite.timeout_seconds == window.TIMEOUT_RANGE[1]
