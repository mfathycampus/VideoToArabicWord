"""مسارات ماك ولينكس تُختبر على لينكس بمحاكاة ``sys.platform``."""
from __future__ import annotations

import sys
from pathlib import Path

from licensing import fingerprint, state
from utils import power

IOREG = '''+-o Root  <class IORegistryEntry>
  | {
  |   "IOPlatformUUID" = "11111111-2222-3333-4444-555555555555"
  | }
'''


def test_parse_ioreg_uuid():
    assert fingerprint.parse_ioreg_uuid(IOREG) == "11111111-2222-3333-4444-555555555555"
    assert fingerprint.parse_ioreg_uuid("nothing") == ""


def test_macos_uuid_only_on_darwin(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert fingerprint._macos_platform_uuid() == ""


def test_fingerprint_is_stable_and_20_hex():
    a = fingerprint.device_fingerprint()
    assert a == fingerprint.device_fingerprint()
    assert len(a) == 20 and int(a, 16) >= 0


def test_state_paths_on_macos(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    for var in ("APPDATA", "PROGRAMDATA", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    expected = tmp_path / "Library" / "Application Support" / "VideoToArabicWord"
    assert state.config_state_path() == expected / "state.json"
    assert state.shadow_state_path() == expected / ".runtime"
    assert state.StateStore("dev").use_registry is False


def test_keep_awake_darwin_runs_caffeinate(monkeypatch):
    calls = []

    class FakeProc:
        def terminate(self):
            calls.append("terminated")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("subprocess.Popen",
                        lambda cmd, **kw: calls.append(cmd) or FakeProc())
    with power.keep_awake("x"):
        calls.append("inside")
    assert calls[0][0].endswith("caffeinate") and calls[1:] == ["inside", "terminated"]


def test_keep_awake_survives_missing_caffeinate(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    def boom(*a, **k):
        raise OSError("no caffeinate")

    monkeypatch.setattr("subprocess.Popen", boom)
    with power.keep_awake("x"):
        pass
