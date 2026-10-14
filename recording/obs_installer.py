"""تنزيل OBS Studio المحمول وفكّه — بإذن المستخدم وبفحص تجزئة.

القواعد:

* **لا تنزيل بلا إذن.** هذه الوحدة لا تسأل؛ من يستدعيها (الواجهة) هو من
  حصل على الموافقة. هذا يطابق ADR-014 لنموذج التفريغ.
* **لا تشغيل لما لم يُتحقَّق منه.** الملف يُنزَّل إلى مجلد عمل مؤقت،
  ويُفحص حجمه وSHA-256 قبل فكّه، وإن لم يطابق يُحذف ولا يُفكّ شيء.
* **فكٌّ آمن.** أي مسار داخل الحزمة يخرج من مجلد الفكّ يُرفض (zip-slip).
* **تثبيتٌ ذرّي.** يُفكّ في مجلد مؤقت ثم يُنقل بخطوة واحدة؛ فانقطاع
  التيار لا يترك نسخة نصف مفكوكة تبدو سليمة (علامة ``maeen_install.json``
  آخر ما يُكتب).
* **بلا صلاحيات مدير.** كل شيء تحت ``%LOCALAPPDATA%``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

from core.exceptions import (
    InsufficientDiskSpaceError,
    PipelineCancelledError,
    RecordingToolDownloadError,
    RecordingToolUnavailableError,
)
from recording.obs_release import ObsRelease, select_release
from utils.cancellation import CancellationToken
from utils.logger import logger
from version import APP_VERSION

ProgressFn = Callable[[float, str], None]

MARKER_NAME = "maeen_install.json"
EXE_NAME = "obs64.exe"

_CHUNK = 256 * 1024
#: نسبة شريط التقدّم المخصّصة للتنزيل؛ الباقي للفكّ.
_DOWNLOAD_SHARE = 0.85
_VERSION_DIR_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")


def install_root() -> Path:
    """مجلد التثبيت: ``%LOCALAPPDATA%\\VideoToArabicWord\\obs``."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA")
        base_path = Path(base) if base else Path.home() / "AppData" / "Local"
    else:
        base_path = Path(os.environ.get(
            "XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base_path / "VideoToArabicWord" / "obs"


def release_dir(release: ObsRelease, root: Optional[Path] = None) -> Path:
    return (Path(root) if root else install_root()) / release.version


def find_exe(directory: Path) -> Optional[Path]:
    """يجد ``obs64.exe`` تحت مجلد (يفضّل ``bin/64bit``)."""
    directory = Path(directory)
    if not directory.is_dir():
        return None
    preferred = directory / "bin" / "64bit" / EXE_NAME
    if preferred.is_file():
        return preferred
    for candidate in sorted(directory.rglob(EXE_NAME)):
        if candidate.is_file():
            return candidate
    return None


def installed_exe(release: Optional[ObsRelease] = None,
                  root: Optional[Path] = None) -> Optional[Path]:
    """مسار ``obs64.exe`` المثبَّت بالتحقق الكامل، أو ``None``."""
    release = release or select_release()
    directory = release_dir(release, root)
    marker = directory / MARKER_NAME
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("sha256") != release.sha256:
        return None
    exe = directory / str(data.get("exe", ""))
    return exe if exe.is_file() else None


def is_installed(release: Optional[ObsRelease] = None,
                 root: Optional[Path] = None) -> bool:
    return installed_exe(release, root) is not None


def portable_root(exe: Path) -> Path:
    """جذر النسخة المحمولة: المجلد الذي فيه ``bin`` (أبو ``bin/64bit``)."""
    return Path(exe).parent.parent.parent


# ----------------------------------------------------------------------
def download_and_install(release: Optional[ObsRelease] = None,
                         root: Optional[Path] = None, *,
                         progress: Optional[ProgressFn] = None,
                         cancel: Optional[CancellationToken] = None,
                         urlopen: Optional[Callable] = None) -> Path:
    """ينزّل OBS ويتحقق منه ويفكّه، ويعيد مسار ``obs64.exe``.

    يرفع ``RecordingToolDownloadError`` لأي فشل شبكة أو سلامة،
    و``PipelineCancelledError`` عند الإلغاء. لا يترك شيئًا ناقصًا خلفه.
    """
    release = release or select_release()
    root = Path(root) if root else install_root()
    urlopen = urlopen or urllib.request.urlopen
    root.mkdir(parents=True, exist_ok=True)

    # الحزمة + المفكوك + هامش: نحو ثلاثة أضعاف حجم الحزمة.
    needed = release.size_bytes * 3
    free = shutil.disk_usage(root).free
    if free < needed:
        raise InsufficientDiskSpaceError(
            f"المساحة الحرة لا تكفي لتثبيت أداة التسجيل: "
            f"مطلوب نحو {needed // (1024 * 1024)} ميغابايت "
            f"والمتاح {free // (1024 * 1024)}.")

    work = root / f".w{os.getpid()}{int(time.time())}"
    work.mkdir(parents=True)
    try:
        zip_path = work / "obs.zip"
        _download(release, zip_path, progress, cancel, urlopen)
        staging = work / "x"
        _extract(zip_path, staging, progress, cancel)
        exe = find_exe(staging)
        if exe is None:
            raise RecordingToolDownloadError(
                f"الحزمة المنزَّلة لا تحوي {EXE_NAME} — لن تُستعمل.")
        _write_portable_markers(exe)
        relative_exe = exe.relative_to(staging)

        target = release_dir(release, root)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        os.replace(staging, target)
        (target / MARKER_NAME).write_text(json.dumps({
            "version": release.version,
            "sha256": release.sha256,
            "exe": relative_exe.as_posix(),
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "installed_by": f"Maeen {APP_VERSION}",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        _prune_old(root, keep=release.version)
        if progress:
            progress(1.0, "اكتمل تثبيت أداة التسجيل.")
        result = installed_exe(release, root)
        if result is None:  # لا يفترض أن يحدث
            raise RecordingToolUnavailableError(
                "اكتمل الفكّ لكن تعذّر العثور على أداة التسجيل.")
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _download(release: ObsRelease, dest: Path, progress: Optional[ProgressFn],
              cancel: Optional[CancellationToken], urlopen: Callable) -> None:
    request = urllib.request.Request(
        release.url, headers={"User-Agent": f"Maeen/{APP_VERSION}"})
    digest = hashlib.sha256()
    done = 0
    try:
        with urlopen(request, timeout=30) as response, open(dest, "wb") as out:
            total = _content_length(response) or release.size_bytes
            while True:
                if cancel is not None:
                    cancel.raise_if_cancelled()
                chunk = response.read(_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(min(done / total, 1.0) * _DOWNLOAD_SHARE,
                             f"تنزيل أداة التسجيل… {done // (1024 * 1024)} "
                             f"من {total // (1024 * 1024)} ميغابايت")
    except PipelineCancelledError:
        raise
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise RecordingToolDownloadError(
            f"تعذّر تنزيل أداة التسجيل من المصدر الرسمي ({exc}). "
            "تحقّق من الاتصال بالإنترنت أو من حجب الشبكة لموقع github.com، "
            "أو ثبّت OBS يدويًا وحدّد مساره في الإعدادات.") from exc

    if done != release.size_bytes:
        raise RecordingToolDownloadError(
            f"حجم الملف المنزَّل ({done}) لا يطابق المتوقع "
            f"({release.size_bytes}) — حُذف ولم يُستعمل.")
    if digest.hexdigest() != release.sha256:
        logger.error(f"تجزئة OBS لا تطابق: {digest.hexdigest()}")
        raise RecordingToolDownloadError(
            "فشل التحقق من سلامة الملف المنزَّل (SHA-256) — حُذف ولم يُستعمل.")


def _content_length(response) -> int:
    try:
        return int(response.headers.get("Content-Length") or 0)
    except (AttributeError, TypeError, ValueError):
        return 0


def _extract(zip_path: Path, dest: Path, progress: Optional[ProgressFn],
             cancel: Optional[CancellationToken]) -> None:
    dest.mkdir(parents=True)
    dest_resolved = dest.resolve()
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = archive.infolist()
            count = max(len(members), 1)
            for index, member in enumerate(members):
                if cancel is not None:
                    cancel.raise_if_cancelled()
                target = (dest / member.filename).resolve()
                if target != dest_resolved and dest_resolved not in target.parents:
                    raise RecordingToolDownloadError(
                        "الحزمة تحوي مسارًا خارج مجلد الفكّ — رُفضت.")
                archive.extract(member, dest)
                if progress and index % 50 == 0:
                    progress(_DOWNLOAD_SHARE
                             + (1 - _DOWNLOAD_SHARE) * index / count,
                             "فكّ أداة التسجيل…")
    except zipfile.BadZipFile as exc:
        raise RecordingToolDownloadError(
            "الملف المنزَّل ليس حزمة zip سليمة.") from exc


def _write_portable_markers(exe: Path) -> None:
    """علامة الوضع المحمول: تُكتب بجوار التنفيذي وفي جذر الحزمة.

    التشغيل نفسه يمرّر ``--portable`` أيضًا؛ العلامتان احتياط لاختلاف
    مواضع البحث بين إصدارات OBS.
    """
    for directory in {exe.parent, portable_root(exe)}:
        try:
            (directory / "portable_mode.txt").write_text("", encoding="utf-8")
        except OSError as exc:  # pragma: no cover - مجلد للقراءة فقط
            logger.warning(f"تعذّرت كتابة علامة الوضع المحمول: {exc}")


def _prune_old(root: Path, keep: str) -> None:
    """يحذف الإصدارات الأقدم وبقايا مجلدات العمل المتروكة."""
    for child in root.iterdir():
        try:
            if not child.is_dir():
                continue
            stale_work = child.name.startswith(".w")
            old_version = (_VERSION_DIR_RE.match(child.name)
                           and child.name != keep)
            if stale_work or old_version:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue
