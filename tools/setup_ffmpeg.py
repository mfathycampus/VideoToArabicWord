"""تنزيل ffmpeg و ffprobe ووضعهما في مجلد bin/ داخل المشروع.

    python tools/setup_ffmpeg.py

لماذا وحدة مستقلة: ``ffprobe`` إلزامي لقراءة بيانات الفيديو (ADR-013)،
وحزمة ``imageio-ffmpeg`` لا تشحنه إطلاقًا. تثبيته يدويًا على ويندوز هو
أكثر خطوة يتعثّر فيها المستخدمون، فهذه الأداة تؤتمتها.

الاتصال الشبكي هنا خطوة إعداد صريحة يشغّلها المستخدم بنفسه، خارج
الـ pipeline الذي يبقى محليًا بالكامل (ADR-002).
"""
from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"

WINDOWS_SOURCES = (
    ("gyan.dev (essentials)",
     "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"),
    ("BtbN (win64-gpl)",
     "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"
     "ffmpeg-master-latest-win64-gpl.zip"),
)

NEEDED = ("ffmpeg", "ffprobe")


def executable_name(stem: str) -> str:
    return f"{stem}.exe" if platform.system() == "Windows" else stem


def already_present() -> bool:
    return all((BIN / executable_name(name)).is_file() for name in NEEDED)


def found_on_path() -> dict[str, Optional[str]]:
    return {name: shutil.which(name) for name in NEEDED}


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "video-ai-doc-setup"})
    with urllib.request.urlopen(request, timeout=120) as response:
        total = int(response.headers.get("content-length") or 0)
        downloaded = 0
        with open(destination, "wb") as handle:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if total:
                    percent = downloaded / total * 100
                    print(f"\r    تنزيل… {percent:5.1f}%  "
                          f"({downloaded / 1e6:.0f}/{total / 1e6:.0f} MB)",
                          end="", flush=True)
    print()


def _extract(archive: Path, targets: Iterable[str]) -> list[str]:
    """يستخرج الملفات المطلوبة أينما كانت داخل الأرشيف."""
    wanted = {executable_name(name).lower() for name in targets}
    extracted: list[str] = []
    BIN.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            leaf = member.rsplit("/", 1)[-1].lower()
            if leaf in wanted:
                with zf.open(member) as source, open(BIN / leaf, "wb") as target:
                    shutil.copyfileobj(source, target)
                (BIN / leaf).chmod(0o755)
                extracted.append(leaf)
    return extracted


def verify() -> bool:
    ok = True
    for name in NEEDED:
        path = BIN / executable_name(name)
        if not path.is_file():
            print(f"  ✗ {name}: غير موجود بعد التنزيل")
            ok = False
            continue
        try:
            result = subprocess.run([str(path), "-version"],
                                    capture_output=True, text=True, timeout=30)
            first = (result.stdout or result.stderr).splitlines()[0][:60]
            print(f"  ✓ {name}: {first}")
        except Exception as exc:
            print(f"  ✗ {name}: لا يعمل ({exc})")
            ok = False
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(
        description="تنزيل ffmpeg و ffprobe إلى bin/")
    parser.add_argument("--force", action="store_true",
                        help="أعد التنزيل حتى لو كانا موجودين")
    args = parser.parse_args()

    print(f"مجلد الوجهة: {BIN}")

    if already_present() and not args.force:
        print("الملفان موجودان في bin/ — لا حاجة للتنزيل.")
        return 0 if verify() else 1

    on_path = found_on_path()
    if all(on_path.values()) and not args.force:
        print("موجودان في PATH:")
        for name, location in on_path.items():
            print(f"  ✓ {name}: {location}")
        print("لا حاجة للتنزيل. (استخدم --force لنسخهما إلى bin/ رغم ذلك.)")
        return 0

    if platform.system() != "Windows":
        print("\nالتنزيل التلقائي مُهيّأ لويندوز فقط.")
        print("على Linux:  sudo apt install ffmpeg")
        print("على macOS:  brew install ffmpeg")
        print("كلاهما يثبّت ffprobe مع ffmpeg.")
        return 1

    last_error: Optional[Exception] = None
    for label, url in WINDOWS_SOURCES:
        print(f"\nالمصدر: {label}")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                archive = Path(tmp) / "ffmpeg.zip"
                _download(url, archive)
                extracted = _extract(archive, NEEDED)
            if len(extracted) >= len(NEEDED):
                print(f"\nاستُخرج: {', '.join(sorted(extracted))}")
                return 0 if verify() else 1
            print(f"    الأرشيف لا يحتوي كل المطلوب (وجد: {extracted})")
        except (urllib.error.URLError, zipfile.BadZipFile, OSError) as exc:
            last_error = exc
            print(f"    فشل: {exc}")

    print("\nتعذر التنزيل التلقائي.")
    if last_error:
        print(f"آخر خطأ: {last_error}")
    print("\nبدائل:")
    print("  1) winget install -e --id Gyan.FFmpeg")
    print("     ثم أعد فتح PowerShell ليُحدَّث PATH")
    print("  2) نزّل يدويًا من https://www.gyan.dev/ffmpeg/builds/")
    print(f"     وضع ffmpeg.exe و ffprobe.exe في: {BIN}")
    return 1


if __name__ == "__main__":
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    from utils.console import enable_utf8_console
    enable_utf8_console()
    raise SystemExit(main())
