"""جلسة تسجيل الاجتماع: الحالات والتنظيف والسياسة (بلا OBS حقيقي)."""
import json
from pathlib import Path

import pytest
from fake_obs import FakeObsClient, FakeProcess

import recording.session as session_module
from config.settings import AppConfig
from core.exceptions import (
    InsufficientDiskSpaceError,
    RecordingControlError,
    RecordingToolDownloadError,
    RecordingToolUnavailableError,
)
from recording.session import MeetingRecorder, RecorderState


class Clock:
    def __init__(self): self.now = 1_000_000.0
    def __call__(self): return self.now


@pytest.fixture
def env(tmp_path, monkeypatch):
    exe = tmp_path / "obs" / "bin" / "64bit" / "obs64.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"x")
    config = AppConfig()
    config.recording.obs_path = str(exe)
    config.recording.output_dir = tmp_path / "rec"
    config.recording.min_free_gb = 0.1
    out_file = tmp_path / "rec" / "Maeen-Recording_test.mkv"

    state = {"client": None, "process": None}

    def make_process(exe_path, args):
        state["process"] = FakeProcess(exe_path, args)
        return state["process"]

    def make_client(host, port, password):
        state["client"] = FakeObsClient(stop_path=out_file)
        state["port"], state["password"] = port, password
        return state["client"]

    clock = Clock()
    events = []
    recorder = MeetingRecorder(
        config, process_factory=make_process, client_factory=make_client,
        on_event=events.append, clock=clock, sleep=lambda _s: None)
    monkeypatch.setattr(session_module.time, "sleep", lambda _s: None)
    return type("Env", (), dict(
        config=config, recorder=recorder, state=state, clock=clock,
        events=events, out_file=out_file, exe=exe, tmp=tmp_path))


def test_full_recording_flow(env):
    rec = env.recorder
    assert rec.state is RecorderState.IDLE and rec.tool_ready()

    rec.start(consent_confirmed=True)
    assert rec.state is RecorderState.RECORDING
    assert env.state["process"].started
    assert env.state["client"].recording
    # كلمة المرور والمنفذ وصلا إلى سطر التشغيل وإلى العميل نفسيهما
    args = env.state["process"].args
    assert args[args.index("--websocket_port") + 1] == str(env.state["port"])
    assert args[args.index("--websocket_password") + 1] == env.state["password"]

    env.clock.now += 125
    assert rec.elapsed() == 125

    result = rec.stop()
    assert rec.state is RecorderState.IDLE
    assert result.path == env.out_file and result.path.is_file()
    assert result.duration_seconds == 125 and not result.recovered
    assert env.state["process"].closed and env.state["client"].closed

    sidecar = json.loads(env.out_file.with_suffix(".recording.json")
                         .read_text(encoding="utf-8"))
    assert sidecar["consent_confirmed"] is True
    assert sidecar["duration_seconds"] == 125.0
    assert sidecar["recorder"].startswith("OBS Studio")
    assert any("بدأ التسجيل" in e for e in env.events)


def test_start_requires_explicit_consent(env):
    with pytest.raises(RecordingControlError, match="إقرار"):
        env.recorder.start(consent_confirmed=False)
    assert env.state["process"] is None            # لم يُشغَّل شيء


def test_cannot_start_twice_or_stop_when_idle(env):
    with pytest.raises(RecordingControlError, match="لا يوجد"):
        env.recorder.stop()
    env.recorder.start(consent_confirmed=True)
    with pytest.raises(RecordingControlError, match="جارٍ"):
        env.recorder.start(consent_confirmed=True)


def test_missing_tool_is_reported(env):
    env.config.recording.obs_path = str(env.tmp / "nope" / "obs64.exe")
    assert not env.recorder.tool_ready()
    with pytest.raises(RecordingToolUnavailableError, match="غير مثبّتة"):
        env.recorder.start(consent_confirmed=True)


def test_policy_disabled_recording_blocks_start_and_install(env):
    env.config.recording.enabled = False
    with pytest.raises(RecordingToolUnavailableError, match="سياسة"):
        env.recorder.start(consent_confirmed=True)
    with pytest.raises(RecordingToolUnavailableError, match="سياسة"):
        env.recorder.install()


def test_policy_download_ban_blocks_install_but_not_central_obs(env):
    env.config.recording.allow_download = False
    assert not env.recorder.can_install()
    with pytest.raises(RecordingToolDownloadError, match="سياسة"):
        env.recorder.install()
    # النسخة المنشورة مركزيًّا (obs_path) تبقى صالحة للتسجيل
    env.recorder.start(consent_confirmed=True)
    assert env.recorder.state is RecorderState.RECORDING


def test_low_disk_space_blocks_start(env):
    env.config.recording.min_free_gb = 10 ** 9
    with pytest.raises(InsufficientDiskSpaceError):
        env.recorder.start(consent_confirmed=True)
    assert env.recorder.state is RecorderState.IDLE


def test_obs_exiting_immediately_cleans_up(env, monkeypatch):
    def dead_process(exe, args):
        proc = FakeProcess(exe, args, alive=False)
        env.state["process"] = proc
        return proc
    env.recorder._process_factory = dead_process
    with pytest.raises(RecordingToolUnavailableError, match="أُغلقت"):
        env.recorder.start(consent_confirmed=True)
    assert env.recorder.state is RecorderState.IDLE
    assert env.state["process"].closed


def test_setup_failure_cleans_up_and_allows_retry(env):
    original = env.recorder._client_factory

    def broken(host, port, password):
        client = original(host, port, password)
        client._GetInputKindList = lambda d: {"inputKinds": []}
        return client
    env.recorder._client_factory = broken
    with pytest.raises(RecordingControlError):
        env.recorder.start(consent_confirmed=True)
    assert env.recorder.state is RecorderState.IDLE
    assert env.state["process"].closed and env.state["client"].closed

    env.recorder._client_factory = original      # المحاولة التالية تنجح
    env.recorder.start(consent_confirmed=True)
    assert env.recorder.state is RecorderState.RECORDING


def test_stop_failure_recovers_latest_file(env):
    env.recorder.start(consent_confirmed=True)
    env.state["client"].fail_stop = True
    env.out_file.write_bytes(b"partial")            # OBS كتب الملف قبل الانقطاع
    env.clock.now += 60

    result = env.recorder.stop()

    assert result.recovered and result.path == env.out_file
    assert env.state["process"].closed
    sidecar = json.loads(env.out_file.with_suffix(".recording.json")
                         .read_text(encoding="utf-8"))
    assert sidecar["recovered_after_error"] is True


def test_stop_with_no_file_anywhere_is_an_error(env):
    env.recorder.start(consent_confirmed=True)
    env.state["client"].fail_stop = True
    with pytest.raises(RecordingControlError, match="لم يُعثر"):
        env.recorder.stop()
    assert env.recorder.state is RecorderState.IDLE


def test_abort_closes_everything(env):
    env.recorder.start(consent_confirmed=True)
    env.recorder.abort()
    assert env.recorder.state is RecorderState.IDLE
    assert env.state["process"].closed and not env.state["client"].recording
