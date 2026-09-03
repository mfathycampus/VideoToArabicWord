"""فحص سريع لحزمة البناء بعد PyInstaller.

يُستخدم بعد البناء للتحقق من وجود الملفات الأساسية بدل الاكتفاء بنجاح أمر
PyInstaller نفسه.
"""
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


def validate_bundle(root: Path) -> list[str]:
    errors: list[str] = []
    exe = root / "VideoToArabicWord.exe"
    if not exe.is_file():
        errors.append(f"missing executable: {exe}")
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        if not (root / name).is_file():
            errors.append(f"missing bundled binary: {name}")
    assets = root / "assets" / "logo.png"
    if not assets.is_file():
        errors.append("missing assets/logo.png")
    return errors


def validate_docx(path: Path) -> None:
    with zipfile.ZipFile(path) as z:
        if z.testzip() is not None or "[Content_Types].xml" not in z.namelist():
            raise ValueError("invalid DOCX/OOXML archive")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--docx", type=Path)
    args = ap.parse_args()
    errors = validate_bundle(args.bundle)
    if args.docx:
        try:
            validate_docx(args.docx)
        except Exception as exc:
            errors.append(str(exc))
    if errors:
        print("BUILD SMOKE: FAIL")
        for e in errors:
            print(f" - {e}")
        return 1
    print("BUILD SMOKE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
