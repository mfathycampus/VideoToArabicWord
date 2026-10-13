"""تثبيت OBS المحمول: التنزيل والتحقق والفكّ الآمن (بلا شبكة)."""
import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from core.exceptions import (
    InsufficientDiskSpaceError,
    PipelineCancelledError,
    RecordingToolDownloadError,
)
from recording import obs_installer
from recording.obs_release import (
    WINDOWS_ARM64,
    WINDOWS_X64,
    ObsRelease,
    select_release,
)
from utils.cancellation import CancellationToken


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _release(tmp_path: Path, payload: bytes, *, version="99.1.0",
             sha=None, size=None) -> ObsRelease:
    source = tmp_path / "src.zip"
    source.write_bytes(payload)
    return ObsRelease(
        version=version, name="obs.zip", url=source.as_uri(),
        sha256=sha or hashlib.sha256(payload).hexdigest(),
        size_bytes=size if size is not None else len(payload))


GOOD = {"bin/64bit/obs64.exe": b"MZ-fake", "data/readme.txt": b"hi"}


def test_install_happy_path(tmp_path):
    release = _release(tmp_path, _zip_bytes(GOOD))
    root = tmp_path / "root"
    calls = []

    exe = obs_installer.download_and_install(
        release, root, progress=lambda f, m: calls.append(f))

    assert exe == root / "99.1.0" / "bin" / "64bit" / "obs64.exe"
    assert exe.read_bytes() == b"MZ-fake"
    assert obs_installer.installed_exe(release, root) == exe
    assert (exe.parent / "portable_mode.txt").is_file()
    assert (root / "99.1.0" / "portable_mode.txt").is_file()
    assert calls and calls[-1] == 1.0 and calls == sorted(calls)
    # لا بقايا مجلدات عمل
    assert [p.name for p in root.iterdir()] == ["99.1.0"]


def test_sha_mismatch_rejected_and_nothing_installed(tmp_path):
    release = _release(tmp_path, _zip_bytes(GOOD), sha="0" * 64)
    root = tmp_path / "root"
    with pytest.raises(RecordingToolDownloadError, match="SHA-256"):
        obs_installer.download_and_install(release, root)
    assert not obs_installer.is_installed(release, root)
    assert list(root.iterdir()) == []


def test_size_mismatch_rejected(tmp_path):
    payload = _zip_bytes(GOOD)
    release = _release(tmp_path, payload, size=len(payload) + 1)
    with pytest.raises(RecordingToolDownloadError, match="حجم"):
        obs_installer.download_and_install(release, tmp_path / "root")


def test_zip_slip_rejected(tmp_path):
    evil = _zip_bytes({"bin/64bit/obs64.exe": b"x", "../evil.txt": b"pwn"})
    release = _release(tmp_path, evil)
    root = tmp_path / "root"
    with pytest.raises(RecordingToolDownloadError, match="خارج"):
        obs_installer.download_and_install(release, root)
    assert not (tmp_path / "evil.txt").exists()
    assert not (root / "evil.txt").exists()


def test_archive_without_obs_exe_rejected(tmp_path):
    release = _release(tmp_path, _zip_bytes({"data/readme.txt": b"hi"}))
    with pytest.raises(RecordingToolDownloadError, match="obs64.exe"):
        obs_installer.download_and_install(release, tmp_path / "root")


def test_not_a_zip_rejected(tmp_path):
    release = _release(tmp_path, b"this is not a zip file at all")
    with pytest.raises(RecordingToolDownloadError, match="zip"):
        obs_installer.download_and_install(release, tmp_path / "root")


def test_unreachable_source_gives_actionable_error(tmp_path):
    release = ObsRelease("99.1.0", "obs.zip",
                         (tmp_path / "missing.zip").as_uri(), "0" * 64, 10)
    with pytest.raises(RecordingToolDownloadError, match="github.com"):
        obs_installer.download_and_install(release, tmp_path / "root")


def test_cancel_stops_download(tmp_path):
    release = _release(tmp_path, _zip_bytes(GOOD))
    token = CancellationToken()
    token.cancel()
    with pytest.raises(PipelineCancelledError):
        obs_installer.download_and_install(
            release, tmp_path / "root", cancel=token)
    assert not obs_installer.is_installed(release, tmp_path / "root")


def test_insufficient_disk_space(tmp_path, monkeypatch):
    release = _release(tmp_path, _zip_bytes(GOOD))
    monkeypatch.setattr(
        obs_installer.shutil, "disk_usage",
        lambda _p: type("U", (), {"free": 10})())
    with pytest.raises(InsufficientDiskSpaceError):
        obs_installer.download_and_install(release, tmp_path / "root")


def test_reinstall_replaces_and_prunes_old_versions(tmp_path):
    root = tmp_path / "root"
    old = _release(tmp_path, _zip_bytes(GOOD), version="98.0.0")
    obs_installer.download_and_install(old, root)
    (root / ".w123stale").mkdir()
    (root / "notes").mkdir()           # ليس إصدارًا: لا يُمسّ

    new = _release(tmp_path, _zip_bytes(GOOD), version="99.1.0")
    obs_installer.download_and_install(new, root)

    names = sorted(p.name for p in root.iterdir())
    assert names == ["99.1.0", "notes"]


def test_marker_must_match_pinned_sha(tmp_path):
    release = _release(tmp_path, _zip_bytes(GOOD))
    root = tmp_path / "root"
    obs_installer.download_and_install(release, root)
    other = ObsRelease(release.version, release.name, release.url,
                       "f" * 64, release.size_bytes)
    assert obs_installer.installed_exe(other, root) is None


def test_half_installed_directory_is_not_installed(tmp_path):
    release = _release(tmp_path, _zip_bytes(GOOD))
    root = tmp_path / "root"
    (root / release.version / "bin" / "64bit").mkdir(parents=True)
    (root / release.version / "bin" / "64bit" / "obs64.exe").write_bytes(b"x")
    assert not obs_installer.is_installed(release, root)   # لا علامة اكتمال


def test_pinned_releases_are_wellformed():
    for release in (WINDOWS_X64, WINDOWS_ARM64):
        assert release.url.startswith(
            "https://github.com/obsproject/obs-studio/releases/download/")
        assert release.version in release.url and release.name in release.url
        assert len(release.sha256) == 64 and int(release.sha256, 16)
        assert release.size_bytes > 100_000_000
    assert select_release("ARM64") is WINDOWS_ARM64
    assert select_release("AMD64") is WINDOWS_X64


def test_version_int_matches_obs_encoding():
    release = ObsRelease("32.2.2", "n", "u", "0" * 64, 1)
    assert release.version_int == (32 << 24) | (2 << 16) | 2
