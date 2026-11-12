"""1.22.5: الدفعة الثانية من تدقيق 12 نوفمبر 2026.

مدخلات البناء مثبّتة (ffmpeg بتجزئة، ملف قفل بتجزئات، إجراءات GitHub بتجزئة
الالتزام)، وحقول النافذة الرئيسية لها أسماء يقرؤها قارئ الشاشة.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


# ---------------------------------------------------------------------
# ffmpeg المشحون: إصدار محدّد وتجزئة
# ---------------------------------------------------------------------
def test_shipped_ffmpeg_sources_are_versioned_and_hashed():
    import tools.setup_ffmpeg as setup

    assert len(setup.WINDOWS_SOURCES) >= 2          # مصدرٌ بديل إن تعطّل الأول
    for label, url, sha256 in setup.WINDOWS_SOURCES:
        assert url.startswith("https://"), label
        assert setup.FFMPEG_VERSION in url, label
        assert "latest" not in url and "release-essentials" not in url, label
        assert re.fullmatch(r"[0-9a-f]{64}", sha256), label
    # المصدران ملفٌّ واحد: تجزئة واحدة، فلا يختلف ما يُشحن بحسب أيّهما أجاب
    assert len({sha for _, _, sha in setup.WINDOWS_SOURCES}) == 1


class _Download:
    def __init__(self, payload: bytes) -> None:
        self._stream = io.BytesIO(payload)
        self.headers = {"content-length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size=-1):
        return self._stream.read(size)


def test_download_with_wrong_hash_is_deleted_and_refused(tmp_path, monkeypatch):
    import tools.setup_ffmpeg as setup

    monkeypatch.setattr(setup.urllib.request, "urlopen",
                        lambda request, timeout=None: _Download(b"tampered archive"))
    target = tmp_path / "ffmpeg.zip"
    with pytest.raises(setup.ChecksumMismatchError):
        setup._download("https://example.test/ffmpeg.zip", target, "0" * 64)
    assert not target.exists()
    assert issubclass(setup.ChecksumMismatchError, OSError)   # حلقة المصادر تلتقطه


def test_download_with_matching_hash_is_kept(tmp_path, monkeypatch):
    import tools.setup_ffmpeg as setup

    payload = b"the real archive"
    monkeypatch.setattr(setup.urllib.request, "urlopen",
                        lambda request, timeout=None: _Download(payload))
    target = tmp_path / "ffmpeg.zip"
    setup._download("https://example.test/ffmpeg.zip", target,
                    hashlib.sha256(payload).hexdigest().upper())
    assert target.read_bytes() == payload


def test_a_bad_source_falls_through_to_the_next_one(tmp_path, monkeypatch):
    """مصدرٌ عُبث بملفّه لا يوقف الإعداد ولا يُستعمل: يُجرَّب التالي."""
    import zipfile

    import tools.setup_ffmpeg as setup

    good = io.BytesIO()
    with zipfile.ZipFile(good, "w") as archive:
        archive.writestr("ffmpeg-x/bin/ffmpeg.exe", b"MZ ffmpeg")
        archive.writestr("ffmpeg-x/bin/ffprobe.exe", b"MZ ffprobe")
    payload = good.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    served = {"https://bad.test/f.zip": b"tampered", "https://good.test/f.zip": payload}
    asked = []

    def urlopen(request, timeout=None):
        asked.append(request.full_url)
        return _Download(served[request.full_url])

    monkeypatch.setattr(setup.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(setup, "WINDOWS_SOURCES", (
        ("bad", "https://bad.test/f.zip", digest),
        ("good", "https://good.test/f.zip", digest)))
    monkeypatch.setattr(setup, "BIN", tmp_path / "bin")
    monkeypatch.setattr(setup.platform, "system", lambda: "Windows")
    monkeypatch.setattr(setup, "found_on_path", lambda: {n: None for n in setup.NEEDED})
    monkeypatch.setattr(setup, "verify", lambda: True)
    monkeypatch.setattr("sys.argv", ["setup_ffmpeg.py"])

    assert setup.main() == 0
    assert asked == ["https://bad.test/f.zip", "https://good.test/f.zip"]
    assert (tmp_path / "bin" / "ffmpeg.exe").read_bytes() == b"MZ ffmpeg"


# ---------------------------------------------------------------------
# البناء: من ملف القفل بتجزئات، وإجراءات مثبّتة بتجزئة الالتزام
# ---------------------------------------------------------------------
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_actions_are_pinned_to_a_commit_not_a_tag(workflow):
    uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)", workflow.read_text(encoding="utf-8"), re.M)
    assert uses, workflow.name
    for action in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", action), action


def test_release_build_installs_only_from_the_hashed_lock():
    build = _text(".github/workflows/build.yml")
    installs = [line.strip() for line in build.splitlines()
                if "pip install" in line and not line.strip().startswith("#")]
    for line in installs:
        assert "--upgrade pip" in line or (
            "--require-hashes" in line and "requirements.lock.txt" in line), line
    assert sum("requirements.lock.txt" in line for line in installs) == 2   # ويندوز وماك
    assert re.search(r"choco install tesseract --version=\d", build)


def _locked() -> dict:
    pins = {}
    for line in _text("requirements.lock.txt").splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", line)
        if match:
            pins[match.group(1).lower().replace("_", "-")] = match.group(2)
    return pins


def test_every_locked_package_carries_hashes():
    blocks = re.split(r"\n(?=[A-Za-z0-9_.-]+==)", _text("requirements.lock.txt"))
    packages = [block for block in blocks if re.match(r"^[A-Za-z0-9_.-]+==", block)]
    assert len(packages) >= 40
    for block in packages:
        assert "--hash=sha256:" in block, block.splitlines()[0]


def test_lock_honours_the_ranges_the_app_declares():
    """قفلٌ تخطّى سقفًا مكتوبًا لسبب (PyAV <19، huggingface_hub <2) يعيد عطله."""
    from packaging.requirements import Requirement

    pins = _locked()
    declared = []
    for name in ("requirements.txt", "requirements-export.txt"):
        for line in _text(name).splitlines():
            line = line.split("#", 1)[0].strip()
            if line and not line.startswith("-"):
                declared.append(Requirement(line))
    assert len(declared) >= 12
    for requirement in declared:
        key = requirement.name.lower().replace("_", "-")
        assert key in pins, f"{requirement.name} ليست في ملف القفل"
        assert requirement.specifier.contains(pins[key], prereleases=True), (
            f"{requirement.name}=={pins[key]} خارج النطاق {requirement.specifier}")
    assert "pyinstaller" in pins                     # أداة البناء نفسها مقفلة


# ---------------------------------------------------------------------
# الوصول: لكل حقل اسم، وللمسار الأساسي اختصارات
# ---------------------------------------------------------------------
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def window():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication

    from config.settings import AppConfig
    from ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow(AppConfig())
    yield win
    win.close()
    del app


def test_every_input_in_the_main_window_has_a_name_for_screen_readers(window):
    """قاعدة Qt: الاسم = accessibleName، أو نصّ الزرّ، أو تسمية مربوطة (buddy)."""
    from PyQt6.QtWidgets import (
        QAbstractButton,
        QAbstractSpinBox,
        QComboBox,
        QLabel,
        QLineEdit,
        QListWidget,
        QPlainTextEdit,
        QSlider,
        QTableWidget,
        QTextEdit,
    )

    buddies = {id(label.buddy()): label.text()
               for label in window.findChildren(QLabel) if label.buddy() is not None}
    kinds = (QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit, QPlainTextEdit,
             QTextEdit, QSlider, QListWidget, QTableWidget)
    unnamed, total = [], 0
    for widget in window.findChildren(kinds):
        if isinstance(widget.parent(), (QAbstractSpinBox, QComboBox)):
            continue                                  # أجزاء داخلية لعنصر مركّب
        if widget.objectName().startswith("qt_"):
            continue
        total += 1
        name = (widget.accessibleName()
                or (widget.text() if isinstance(widget, QAbstractButton) else "")
                or buddies.get(id(widget), ""))
        if not name.strip():
            attr = next((k for k, v in vars(window).items() if v is widget), "?")
            unnamed.append(f"{type(widget).__name__}:{attr}")
    assert total >= 50
    assert not unnamed, f"حقول بلا اسم لقارئ الشاشة: {unnamed}"


def test_accessible_names_point_at_real_widgets(window):
    for attr in window.ACCESSIBLE_NAMES:
        assert getattr(window, attr, None) is not None, attr


def test_main_path_has_keyboard_shortcuts_and_cancel_has_none(window):
    from PyQt6.QtGui import QShortcut

    keys = {shortcut.key().toString() for shortcut in window.findChildren(QShortcut)}
    assert {"Ctrl+O", "Ctrl+Return", "Ctrl+Shift+O"} <= keys
    assert all(attr != "cancel_button" for attr, _ in window.SHORTCUTS)
    assert "Ctrl+O" in window.browse_button.toolTip()      # يُكتشف من التلميح


def test_shortcut_clicks_its_button_and_survives_a_label_change(window):
    from PyQt6.QtGui import QShortcut

    toggle = next(shortcut for shortcut in window.findChildren(QShortcut)
                  if shortcut.key().toString() == "Ctrl+L")
    before = window.log_toggle.isChecked()
    toggle.activated.emit()
    assert window.log_toggle.isChecked() is (not before)
    window.log_toggle.setText("نصّ آخر")                    # كما تفعل الواجهة
    toggle.activated.emit()
    assert window.log_toggle.isChecked() is before


def test_shortcut_does_nothing_while_its_button_is_disabled(window):
    from PyQt6.QtGui import QShortcut

    assert not window.start_button.isEnabled()              # لا ملف مختار بعد
    started = []
    window.start_button.clicked.connect(lambda: started.append(1))
    start = next(shortcut for shortcut in window.findChildren(QShortcut)
                 if shortcut.key().toString() == "Ctrl+Return")
    start.activated.emit()
    assert started == []
