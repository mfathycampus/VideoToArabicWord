"""يولّد ``requirements.lock.txt`` — إصدارات مقفلة قابلة لإعادة الإنتاج.

المواصفة §28 تشترط ملفًا مقفلًا. ما لم تكن تنص عليه — ولا تستطيع —
هو **قفل واحد يصلح للجميع**: القفل مرتبط بالمفسّر ونظام التشغيل.
‏``numpy`` مثلًا لا تملك عجلات 1.x لـ Python 3.13، و ``scikit-image``
الحديثة تشترط 3.11+. قفلٌ وُلِّد على 3.12 يفشل حرفيًا على 3.10.

لذلك يُولَّد القفل **على جهاز البناء نفسه**:

    python tools/lock_requirements.py

ويُثبَّت منه:

    python -m pip install -r requirements.lock.txt

الملف الناتج يحمل في رأسه المفسّر والنظام اللذين وُلِّد عليهما، فلا
يُستهلك بالخطأ في بيئة مختلفة.
"""
from __future__ import annotations

import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "requirements.lock.txt"


def main() -> int:
    try:
        import piptools  # noqa: F401
    except ImportError:
        print("pip-tools غير مثبّتة. ثبّتها بـ:\n"
              f'  "{sys.executable}" -m pip install pip-tools',
              file=sys.stderr)
        return 2

    header = (
        f"# وُلِّد بـ tools/lock_requirements.py\n"
        f"# المفسّر: Python {platform.python_version()} · "
        f"النظام: {platform.system()} {platform.machine()}\n"
        f"# التاريخ: {datetime.now():%Y-%m-%d}\n"
        f"# ⚠ صالح لهذه البيئة فقط — أعد التوليد على أي بيئة أخرى.\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "piptools", "compile",
         "--quiet", "--strip-extras", "--no-header",
         "--output-file", str(OUTPUT), str(ROOT / "requirements.in")],
        cwd=str(ROOT))
    if result.returncode != 0:
        return result.returncode

    OUTPUT.write_text(header + OUTPUT.read_text(encoding="utf-8"),
                      encoding="utf-8")
    print(f"تم: {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
