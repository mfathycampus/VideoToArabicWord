"""يجهّز Tesseract لشحنه داخل الحزمة — ``bin/tesseract/``.

    python tools/setup_tesseract.py [--source "C:/Program Files/Tesseract-OCR"]

سبب وجوده: على جهاز معلّمٍ نظيف لا Tesseract، فيتعطّل بصمت كلّ ما
يعتمد على قراءة الشاشة: **تمويه أسماء الطلاب** في اللقطات، وقصّ شريط
المتصفّح برابطه، ومصطلحات الشاشة. أن نطلب من المعلّم تثبيت برنامجٍ
ثانٍ — ثم تأشير خيار PATH الذي لا يؤشّره المثبِّت افتراضيًّا — هو
بالضبط ما رفضه المستخدم: «لا يتطلّب من المستخدم تثبيت شيء».

يُنسخ من تثبيتٍ موجود (‏CI: ‏``choco install tesseract``) ما يلزم
التشغيل وحده: الملف التنفيذي ومكتباته، وحزم ``eng`` و``ara`` و``osd``.
أدوات التدريب والوثائق لا تُشحن. وحزمة العربية — إن غابت من المصدر —
تُنزَّل من ``tessdata_fast`` الرسمي (أصغر وأسرع، ودقّته كافية للواجهات).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "bin" / "tesseract"
LANGS = ("eng", "ara", "osd")
TESSDATA_URL = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/{lang}.traineddata"
#: ما لا يلزم التشغيل — أدوات تدريب Tesseract.
SKIP_EXE_PREFIXES = ("unicharset", "text2image", "lstm", "classifier",
                     "cntraining", "mftraining", "shapeclustering",
                     "wordlist2dawg", "dawg2wordlist", "combine_", "ambiguous",
                     "set_unicharset", "merge_unicharsets", "unins")


def _default_source() -> Path | None:
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if base and (Path(base) / "Tesseract-OCR" / "tesseract.exe").is_file():
            return Path(base) / "Tesseract-OCR"
    found = shutil.which("tesseract")
    return Path(found).parent if found else None


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "vtaw-setup"})
    with urllib.request.urlopen(request, timeout=120) as response:
        destination.write_bytes(response.read())


def prepare(source: Path, target: Path = TARGET) -> Path:
    if not (source / "tesseract.exe").is_file() and not (source / "tesseract").is_file():
        raise SystemExit(f"لا tesseract في {source}")
    if target.exists():
        shutil.rmtree(target)
    (target / "tessdata").mkdir(parents=True)
    for item in source.iterdir():
        name = item.name.lower()
        if item.is_file() and (name.endswith(".dll") or name == "tesseract.exe"
                               or name == "tesseract"):
            if not name.startswith(SKIP_EXE_PREFIXES):
                shutil.copy2(item, target / item.name)
    tessdata = source / "tessdata"
    for lang in LANGS:
        file = tessdata / f"{lang}.traineddata"
        destination = target / "tessdata" / file.name
        if file.is_file():
            shutil.copy2(file, destination)
        else:
            print(f"تنزيل حزمة {lang} من tessdata_fast…")
            _download(TESSDATA_URL.format(lang=lang), destination)
    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    print(f"جاهز: {target} ({size / 1e6:.0f} MB)")
    return target


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from utils.console import enable_utf8_console
    enable_utf8_console()

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=None)
    args = parser.parse_args()
    source = args.source or _default_source()
    if source is None:
        print("لم يُعثر على Tesseract مثبَّتًا — ثبّته أولًا "
              "(ويندوز: choco install tesseract).", file=sys.stderr)
        return 1
    prepare(source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
