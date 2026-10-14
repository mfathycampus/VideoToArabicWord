"""سياسة الجهاز وإعداد التسجيل: التقييد فقط، والإعداد القديم يبقى صالحًا."""
from pathlib import Path

from config.policy import apply, load
from config.settings import AppConfig


def _policy(tmp_path: Path, body: str):
    path = tmp_path / "policy.yaml"
    path.write_text(body, encoding="utf-8")
    return load(path)


def test_policy_can_disable_meeting_recording(tmp_path):
    policy = _policy(tmp_path, "allow_meeting_recording: false\nnotice: x\n")
    config = AppConfig()
    apply(config, policy)
    assert config.recording.enabled is False
    assert "allow_meeting_recording" in policy.locked


def test_policy_can_disable_obs_download_only(tmp_path):
    policy = _policy(tmp_path, "allow_obs_download: false\n")
    config = AppConfig()
    apply(config, policy)
    assert config.recording.enabled is True
    assert config.recording.allow_download is False


def test_policy_true_never_enables_anything(tmp_path):
    policy = _policy(tmp_path,
                     "allow_meeting_recording: true\nallow_obs_download: true\n")
    config = AppConfig()
    config.recording.enabled = False
    config.recording.allow_download = False
    apply(config, policy)
    assert config.recording.enabled is False
    assert config.recording.allow_download is False


def test_config_without_recording_section_loads_with_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("application:\n  language: ar\n", encoding="utf-8")
    config = AppConfig.load(path)
    assert config.recording.enabled and config.recording.allow_download
    assert config.recording.video_width == 1600 and config.recording.video_fps == 10
    assert config.recording.resolved_output_dir().name == "Maeen Recordings"


def test_recording_section_roundtrips_through_save(tmp_path):
    path = tmp_path / "config.yaml"
    config = AppConfig()
    config.recording.capture_microphone = False
    config.recording.output_dir = tmp_path / "rec"
    config.save(path)
    loaded = AppConfig.load(path)
    assert loaded.recording.capture_microphone is False
    assert loaded.recording.output_dir == tmp_path / "rec"
