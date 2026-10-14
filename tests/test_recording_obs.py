"""إعداد OBS المحمول، وبروتوكول obs-websocket، وتهيئة المشهد."""
import base64
import configparser
import hashlib
import json
import socket
from pathlib import Path

import pytest
from fake_obs import FakeObsClient, FakeSocket

from core.exceptions import RecordingControlError
from recording import obs_config
from recording.obs_controller import (
    CaptureSettings,
    ObsRecorder,
    ObsRequestFailed,
    ObsWebSocket,
    compute_auth,
)
from recording.obs_release import ObsRelease

RELEASE = ObsRelease("32.2.2", "n", "u", "0" * 64, 1)


def _exe(tmp_path: Path) -> Path:
    exe = tmp_path / "obs" / "bin" / "64bit" / "obs64.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"x")
    return exe


# ---------------------------------------------------------------- config
def test_seed_config_writes_websocket_and_skips_wizard(tmp_path):
    exe = _exe(tmp_path)
    base = obs_config.seed_config(exe, RELEASE, 4567, "pw-123")

    assert base == tmp_path / "obs" / "config" / "obs-studio"
    ws = json.loads((base / "plugin_config" / "obs-websocket"
                     / "config.json").read_text())
    assert ws["server_enabled"] is True and ws["auth_required"] is True
    assert ws["server_port"] == 4567 and ws["server_password"] == "pw-123"
    assert ws["first_load"] is False and ws["alerts_enabled"] is False

    for name in ("global.ini", "user.ini"):
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(base / name, encoding="utf-8")
        assert parser["General"]["FirstRun"] == "true"
        assert int(parser["General"]["LastVersion"]) == RELEASE.version_int


def test_seed_config_preserves_existing_ini_keys_and_case(tmp_path):
    exe = _exe(tmp_path)
    base = obs_config.config_dir(exe)
    base.mkdir(parents=True)
    (base / "global.ini").write_text(
        "[General]\nLanguage=ar-SA\n[Video]\nBaseCX=1920\n", encoding="utf-8")

    obs_config.seed_config(exe, RELEASE, 1, "p")

    text = (base / "global.ini").read_text(encoding="utf-8")
    assert "Language=ar-SA" in text and "BaseCX=1920" in text
    assert "FirstRun=true" in text          # حالة الأحرف محفوظة


def test_seed_config_rewrites_port_and_password_each_session(tmp_path):
    exe = _exe(tmp_path)
    obs_config.seed_config(exe, RELEASE, 1111, "a")
    base = obs_config.seed_config(exe, RELEASE, 2222, "b")
    ws = json.loads((base / "plugin_config" / "obs-websocket"
                     / "config.json").read_text())
    assert (ws["server_port"], ws["server_password"]) == (2222, "b")


def test_launch_args(tmp_path):
    exe = _exe(tmp_path)
    args = obs_config.launch_args(exe, 4455, "pw")
    assert args[0] == str(exe)
    for flag in ("--portable", "--multi", "--minimize-to-tray",
                 "--disable-updater", "--disable-shutdown-check"):
        assert flag in args
    assert args[args.index("--websocket_port") + 1] == "4455"
    assert args[args.index("--websocket_password") + 1] == "pw"


def test_free_port_is_bindable_and_passwords_differ():
    port = obs_config.find_free_port()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))
    assert obs_config.new_password() != obs_config.new_password()
    assert len(obs_config.new_password()) >= 20


# -------------------------------------------------------------- protocol
def test_compute_auth_matches_protocol_definition():
    secret = base64.b64encode(hashlib.sha256(b"pwSALT").digest()).decode()
    expected = base64.b64encode(
        hashlib.sha256((secret + "CHAL").encode()).digest()).decode()
    assert compute_auth("pw", "SALT", "CHAL") == expected


def test_open_authenticates_and_request_roundtrip():
    sock = FakeSocket("s3cret", {"GetVersion": lambda d: {"obsVersion": "32"}})
    client = ObsWebSocket("127.0.0.1", 1, "s3cret", connect=lambda url, timeout: sock)
    client.open()
    assert sock.identified and client.websocket_version == "5.6.0"
    assert client.request("GetVersion") == {"obsVersion": "32"}


def test_wrong_password_fails_cleanly():
    sock = FakeSocket("right", {})
    client = ObsWebSocket("127.0.0.1", 1, "wrong", connect=lambda url, timeout: sock)
    with pytest.raises(RecordingControlError):
        client.open()
    assert sock.closed


def test_failed_request_carries_obs_code():
    sock = FakeSocket("p", {"GetSceneItemId": lambda d: False})
    client = ObsWebSocket("h", 1, "p", connect=lambda u, t: sock)
    client.open()
    with pytest.raises(ObsRequestFailed) as info:
        client.request("GetSceneItemId", {"sourceName": "x"})
    assert info.value.code == 600


def test_unknown_request_and_dropped_connection():
    sock = FakeSocket("p", {})
    client = ObsWebSocket("h", 1, "p", connect=lambda u, t: sock)
    client.open()
    with pytest.raises(ObsRequestFailed) as info:
        client.request("NoSuchThing")
    assert info.value.code == 204
    sock.closed = True
    with pytest.raises(RecordingControlError, match="انقطع"):
        client.request("GetVersion")


# ---------------------------------------------------------------- recorder
def _recorder(client):
    return ObsRecorder(client, sleep=lambda _s: None)


def _settings(tmp_path, **kw):
    return CaptureSettings(record_dir=tmp_path / "rec", **kw)


def test_prepare_builds_profile_scene_sources_and_output(tmp_path):
    fake = FakeObsClient(source=(2560, 1440))
    _recorder(fake).prepare(_settings(tmp_path, video_width=1600, video_fps=10))

    assert fake.profile == "Maeen" and fake.collection == "Maeen"
    assert fake.scenes == ["Maeen"]
    assert set(fake.inputs) == {"Maeen Screen", "Maeen Desktop Audio",
                                "Maeen Microphone"}
    # كل الصوت على المسار 1 فقط — خط المعالجة يقرأ أول مسار
    for name in ("Maeen Desktop Audio", "Maeen Microphone"):
        assert fake.inputs[name]["tracks"] == {
            "1": True, "2": False, "3": False, "4": False, "5": False, "6": False}
        assert fake.inputs[name]["muted"] is False
    # الشاشة الرئيسية مختارة صراحةً (وإلا تُلتقط شاشة وهمية)
    assert fake.inputs["Maeen Screen"]["settings"]["monitor_id"].endswith("DISPLAY1")
    assert fake.transform["boundsType"] == "OBS_BOUNDS_SCALE_INNER"
    assert fake.transform["boundsWidth"] == 1920.0
    # الدقة والإخراج لا يُغيَّران وقت التشغيل (يُكتبان قبل الإقلاع)
    assert "SetVideoSettings" not in fake.log
    assert "SetProfileParameter" not in fake.log


def test_no_monitor_found_is_a_clear_error(tmp_path):
    fake = FakeObsClient()
    fake.monitors = []
    with pytest.raises(RecordingControlError, match="شاشة"):
        _recorder(fake).prepare(_settings(tmp_path))


def test_profile_not_loaded_is_detected(tmp_path):
    fake = FakeObsClient()
    fake.params[("SimpleOutput", "RecQuality")] = "Stream"
    with pytest.raises(RecordingControlError, match="RecQuality"):
        _recorder(fake).prepare(_settings(tmp_path))


def test_canvas_and_output_sizes_are_even_and_never_upscale():
    assert obs_config.canvas_size(1367, 769) == (1366, 768)
    assert obs_config.output_size(2560, 1440, 1600) == (1600, 900)
    assert obs_config.output_size(1280, 720, 1600) == (1280, 720)
    w, h = obs_config.output_size(1367, 769, 1000)
    assert w % 2 == 0 and h % 2 == 0


def test_seed_profile_writes_recording_settings_before_launch(tmp_path):
    exe = tmp_path / "obs" / "32.2.2" / "bin" / "64bit" / "obs64.exe"
    exe.parent.mkdir(parents=True)
    path = obs_config.seed_profile(
        exe, Path("C:\\Users\\x\\Videos"), video_width=1600, video_fps=10)
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(path, encoding="utf-8-sig")
    assert parser["SimpleOutput"]["RecQuality"] == "Small"
    assert parser["SimpleOutput"]["RecFormat2"] == "mkv"
    assert "\\" not in parser["SimpleOutput"]["FilePath"]
    assert parser["Video"]["OutputCX"] == "1600" and parser["Video"]["FPSNum"] == "10"
    assert path.parent.name == "Maeen"
    args = obs_config.launch_args(exe, 1, "p")
    assert args[args.index("--profile") + 1] == "Maeen"


def test_log_tail_returns_error_lines_only(tmp_path):
    from recording import obs_log
    exe = tmp_path / "obs" / "32.2.2" / "bin" / "64bit" / "obs64.exe"
    logs = obs_config.config_dir(exe) / "logs"
    logs.mkdir(parents=True)
    (logs / "a.txt").write_text("info ok\nError: boom\nfine\nStarting the output failed\n",
                                encoding="utf-8")
    tail = obs_log.log_tail(exe)
    assert "boom" in tail and "fine" not in tail
    assert obs_log.log_tail(tmp_path / "none" / "a" / "b" / "c.exe") == ""


def test_disabled_audio_sources_are_muted_not_removed(tmp_path):
    fake = FakeObsClient()
    _recorder(fake).prepare(_settings(
        tmp_path, capture_system_audio=False, capture_microphone=True))
    assert fake.inputs["Maeen Desktop Audio"]["muted"] is True
    assert fake.inputs["Maeen Microphone"]["muted"] is False


def test_prepare_is_idempotent(tmp_path):
    fake = FakeObsClient()
    _recorder(fake).prepare(_settings(tmp_path))
    created = fake.log.count("CreateInput")
    profiles, scenes = list(fake.profiles), list(fake.scenes)

    _recorder(fake).prepare(_settings(tmp_path))

    assert fake.log.count("CreateInput") == created == 3
    assert fake.profiles == profiles and fake.scenes == scenes
    assert fake.log.count("CreateProfile") == 1


def test_existing_source_missing_from_scene_is_added_to_it(tmp_path):
    fake = FakeObsClient()
    _recorder(fake).prepare(_settings(tmp_path))
    del fake.scene_items["Maeen Microphone"]       # موجود كمصدر، ليس في المشهد
    _recorder(fake).prepare(_settings(tmp_path))
    assert "CreateSceneItem" in fake.log
    assert "Maeen Microphone" in fake.scene_items


def test_missing_screen_capture_kind_is_reported(tmp_path):
    fake = FakeObsClient()
    fake._GetInputKindList = lambda d: {"inputKinds": []}
    with pytest.raises(RecordingControlError, match="التقاط الشاشة"):
        _recorder(fake).prepare(_settings(tmp_path))


def test_start_and_stop_return_obs_path(tmp_path):
    out = tmp_path / "x.mkv"
    fake = FakeObsClient(stop_path=out)
    rec = _recorder(fake)
    rec.prepare(_settings(tmp_path))
    rec.start()
    assert fake.recording is True
    assert rec.stop() == str(out)
    assert fake.recording is False and out.is_file()


def test_start_times_out_if_obs_never_reports_active(tmp_path):
    fake = FakeObsClient()
    fake._StartRecord = lambda d: {}          # لا يبدأ فعليًا
    ticks = iter(range(0, 1000, 5))
    rec = ObsRecorder(fake, sleep=lambda _s: None, clock=lambda: next(ticks))
    with pytest.raises(RecordingControlError, match="لم يبدأ"):
        rec.start(timeout=10)
