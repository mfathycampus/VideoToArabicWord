"""فحص الحزمة بعد PyInstaller — يُشغّلها، لا يكتفي بوجودها.

سبب إعادة كتابة هذا الملف: أخطر عطل في تاريخ المشروع كان أن الحزمة
المغلَّفة **لا تفتح إطلاقًا** على جهاز بلا ffmpeg — فحص الثنائيات كان
يبني مسارًا لا وجود له داخل الحزمة، فيُرفَع ``SystemExit`` قبل أن توجد
نافذة، والبناء بلا طرفية. نقرتان ولا شيء يحدث.

ولم يكن لـ CI أن يمسكه: خطوة «تشغيل تجريبي» كانت تتحقّق من **وجود**
الملف التنفيذي فقط. الوجود لا يعني الإقلاع.

عطلان مستقلّان كانا هنا:

1. **التخطيط.** ‏PyInstaller 6 يضع كل شيء تحت ``_internal/`` عدا الملف
   التنفيذي نفسه، بينما 5 يضع الكل مسطّحًا. هذا الملف كان يفترض
   التسطيح، فيبلّغ عن ثنائيات مفقودة في حزمة سليمة تمامًا.
2. **عدم التشغيل.** لا شيء هنا كان يشغّل الحزمة. الآن نستدعي
   ``--selftest`` الذي يفحص البيئة بمسارات الحزمة الحقيقية ويُعيد رمز
   خروج.

    python tools/build_smoke.py dist/VideoToArabicWord
    python tools/build_smoke.py dist/VideoToArabicWord --docx out.docx
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import zipfile
from pathlib import Path

#: مهلة الفحص الذاتي. يشمل استيراد PyQt6 وctranslate2 من قرص بارد على
#: عدّاء ويندوز — أبطأ بكثير من جهاز حقيقي.
SELFTEST_TIMEOUT = 180

EXE_STEM = "VideoToArabicWord"
BUNDLED_BINARIES = ("ffmpeg", "ffprobe")

#: ملفّات بيانات تشحنها حزم طرف ثالث ولا يراها محلّل الاستيراد.
#: ``faster_whisper/assets/silero_vad_v6.onnx`` سقط من الحزمة فعلًا،
#: فكان كل تفريغ ينهار بـNO_SUCHFILE بعد استخراج الصوت. مكرَّر هنا لا
#: مستورَدًا لأن هذا الملفّ يُشغَّل كنصٍّ مستقلّ قبل أن يصير جذر
#: المشروع على ``sys.path``؛ و``tests/test_vad_asset.py`` يحرس تطابقه
#: مع ``utils.deps.VAD_MODEL_NAME``.
BUNDLED_DATA_FILES = ("faster_whisper/assets/silero_vad_v6.onnx",)


def _exe_name() -> str:
    return f"{EXE_STEM}.exe" if sys.platform == "win32" else EXE_STEM


def payload_dir(root: Path) -> Path:
    """المجلد الذي تعيش فيه الثنائيات والبيانات المشحونة.

    ‏PyInstaller 6 (onedir): ``<root>/_internal``. الإصدارات الأقدم:
    ``<root>`` نفسه. نكتشف الحالة بدل أن نفترضها — الافتراض هو ما جعل
    هذا الفحص يبلّغ عن أعطال وهمية في حزمة سليمة.
    """
    internal = root / "_internal"
    return internal if internal.is_dir() else root


def validate_layout(root: Path) -> list[str]:
    """يتحقّق من وجود ما تحتاجه الحزمة فعلًا وقت التشغيل."""
    errors: list[str] = []

    exe = root / _exe_name()
    if not exe.is_file():
        errors.append(f"لا ملف تنفيذي: {exe}")

    payload = payload_dir(root)
    suffix = ".exe" if sys.platform == "win32" else ""
    for name in BUNDLED_BINARIES:
        if not (payload / f"{name}{suffix}").is_file():
            # ffprobe إلزامي (ADR-013)، وبلا ffmpeg مشحون يعتمد التطبيق
            # على PATH — وهو غائب على جهاز المستخدم النهائي بالضبط.
            errors.append(f"ثنائي مفقود من الحزمة: {name}{suffix} في {payload}")

    if not (payload / "assets" / "logo.png").is_file():
        errors.append(f"assets/logo.png مفقود من {payload}")

    for relative in BUNDLED_DATA_FILES:
        if not (payload / Path(relative)).is_file():
            errors.append(
                f"ملفّ بيانات مفقود من الحزمة: {relative} — "
                "المكتبة موجودة ونموذجها ليس معها، فيسقط التفريغ "
                "بـNO_SUCHFILE عند أول تشغيل")

    return errors


def run_selftest(root: Path) -> list[str]:
    """يشغّل الحزمة بوضع الفحص الذاتي — الفحص الوحيد الذي يُثبت الإقلاع."""
    exe = root / _exe_name()
    if not exe.is_file():
        return []                      # سبق التبليغ عنه في فحص التخطيط
    try:
        result = subprocess.run(
            [str(exe), "--selftest"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=SELFTEST_TIMEOUT)
    except subprocess.TimeoutExpired:
        return [f"الفحص الذاتي تجاوز {SELFTEST_TIMEOUT} ثانية بلا نتيجة"]
    except OSError as exc:
        return [f"تعذّر تشغيل الحزمة: {exc}"]

    if result.returncode != 0:
        detail = (result.stdout or result.stderr or "").strip()
        return ["الحزمة لا تُقلع — الفحص الذاتي فشل:",
                *(f"    {line}" for line in detail.splitlines()[-25:])]
    print(result.stdout.strip())
    return []


def validate_docx(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        if (archive.testzip() is not None
                or "[Content_Types].xml" not in archive.namelist()):
            raise ValueError("أرشيف DOCX/OOXML غير سليم")


def main() -> int:
    parser = argparse.ArgumentParser(description="فحص حزمة PyInstaller")
    parser.add_argument("bundle", type=Path, help="مجلد الحزمة (dist/…)")
    parser.add_argument("--docx", type=Path, help="مستند ناتج للتحقّق منه")
    parser.add_argument("--skip-run", action="store_true",
                        help="فحص التخطيط فقط، بلا تشغيل الحزمة")
    args = parser.parse_args()

    errors = validate_layout(args.bundle)
    if not args.skip_run:
        errors += run_selftest(args.bundle)

    if args.docx:
        try:
            validate_docx(args.docx)
        except Exception as exc:
            errors.append(f"المستند: {exc}")

    if errors:
        print("BUILD SMOKE: FAIL")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("BUILD SMOKE: PASS")
    return 0


if __name__ == "__main__":
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    from utils.console import enable_utf8_console
    enable_utf8_console()
    raise SystemExit(main())
