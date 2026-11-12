"""يولّد ``requirements.lock.txt`` — ما تُبنى منه الحزمة التنفيذية بالضبط.

    python tools/lock_requirements.py            # يضيف ما نقص ويُبقي المثبَّت
    python tools/lock_requirements.py --upgrade  # يرفع كل شيء إلى الأحدث

**لماذا قفل (1.22.5):** البناء كان يثبّت من نطاقات (``PyQt6>=6.5,<7``)، فكل
إصدار موسوم يحمل ما صادف أنه الأحدث يوم بنائه — وهكذا دخل
``huggingface_hub 2.x`` بناء 1.16.1 فأسقط التفريغ على أجهزة المعلّمين. ملف
القفل يثبّت كل حزمة برقمها **وتجزئتها**، والبناء يثبّت بـ ``--require-hashes``:
حزمةٌ تغيّر محتواها بعد القفل تُرفض.

**ولماذا قفل واحد لا قفل لكل جهاز:** القفل السابق (pip-tools) كان صالحًا
للمفسّر والنظام اللذين وُلِّد عليهما وحدهما، فلم يُلتزم به قط في المستودع.
``uv`` يحلّ حلًّا «شاملًا»: ملف واحد بعلامات بيئة (``sys_platform == 'win32'``)
يصلح لويندوز وماك ولينكس على مفسّر البناء (Python 3.12) وما بعده.

الاختبارات في CI تبقى على ``requirements-dev.txt`` بلا قفل عمدًا — هي التي
تكشف أن إصدارًا جديدًا يكسر شيئًا قبل أن يدخل هذا الملف.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "requirements-build.in"
OUTPUT = ROOT / "requirements.lock.txt"

#: مفسّر البناء في ``.github/workflows/build.yml``. القفل يصلح له ولما بعده.
BUILD_PYTHON = "3.12"

HEADER = (
    "# ملف قفل البناء — يولَّد ولا يُحرَّر باليد.\n"
    "#   المصدر:   requirements-build.in\n"
    "#   التوليد:  python tools/lock_requirements.py   (--upgrade للترقية)\n"
    "#   التثبيت:  python -m pip install --require-hashes -r requirements.lock.txt\n"
    f"#   يصلح لـ:  Python {BUILD_PYTHON}+ على ويندوز وماك ولينكس\n"
    "#   آخر توليد: {date}\n"
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="يولّد ملف قفل البناء بتجزئات")
    parser.add_argument("--upgrade", action="store_true",
                        help="ارفع كل الحزم إلى أحدث إصدار تسمح به النطاقات")
    args = parser.parse_args(argv)

    try:
        import uv  # noqa: F401
    except ImportError:
        print("uv غير مثبّتة. ثبّتها بـ:\n"
              f'  "{sys.executable}" -m pip install uv', file=sys.stderr)
        return 2

    command = [sys.executable, "-m", "uv", "pip", "compile", SOURCE.name,
               "--universal", "--python-version", BUILD_PYTHON,
               "--generate-hashes", "--no-header", "--quiet",
               "--output-file", OUTPUT.name]
    if args.upgrade:
        command.append("--upgrade")
    result = subprocess.run(command, cwd=str(ROOT))
    if result.returncode != 0:
        return result.returncode

    body = OUTPUT.read_text(encoding="utf-8")
    OUTPUT.write_text(HEADER.format(date=f"{datetime.now():%Y-%m-%d}") + body,
                      encoding="utf-8", newline="\n")
    print(f"تم: {OUTPUT}")
    return 0


if __name__ == "__main__":
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    from utils.console import enable_utf8_console
    enable_utf8_console()
    raise SystemExit(main())
