"""مولّد عقود التفعيل بلا إنترنت — نسخة مبسّطة من ``make_license.py offline``
للاستعمال المتكرّر.

المشكلة التي يحلّها: ``make_license.py offline`` يحتاج ``--code`` و
``--device`` و``--days`` كوسائط منفصلة تُكتب يدويًّا في كل مرّة يرسل
فيها معلّمٌ بصمة جهازه. وزرّ «نسخ» في شاشة التفعيل (``ui/license_dialog.py``)
ينسخ الكود والبصمة **معًا في سطر واحد** — فما يصل إليك من المعلّم لصقةٌ
واحدة، لا قيمتان.

هذا السكربت يأخذ تلك اللصقة كما هي، يسأل عن عدد الأيام، ويطبع عقدًا
جاهزًا (``VTAW1....``) لنسخه وإرساله كما هو.

الاستعمال:
    # تفاعليًّا — يسأل عن اللصقة وعدد الأيام
    python tools/offline_lease.py

    # أو مباشرة: اللصقة وسيطًا واحدًا، وعدد الأيام بعلَم
    python tools/offline_lease.py "VTAW-XXXX-XXXX-XXXX-XXXX 6e610c46221ca230cee2" --days 7

    # أو الكود والبصمة منفصلين، إن لم يصلك الكود مع اللصقة
    python tools/offline_lease.py --code VTAW-XXXX-XXXX-XXXX-XXXX --device 6e610c46221ca230cee2 --days 7

يحتاج ``license_private.key`` بجانب المشروع — انظر ``make_license.py init``.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from licensing.lease import Lease  # noqa: E402
from licensing.signing import sign  # noqa: E402
from tools.make_license import _new_code, _private_key  # noqa: E402
from utils.console import enable_utf8_console  # noqa: E402

# بصمة الجهاز نصٌّ سداسي عشري (``licensing/fingerprint.py``) — الكود
# دائمًا يبدأ بـ«VTAW-» فلا التباس بينهما مهما كان ترتيب اللصقة.
_DEVICE_RE = re.compile(r"^[0-9a-f]{12,64}$", re.IGNORECASE)


def _split_pasted(text: str) -> tuple[str, str]:
    """يفصل لصقة «الكود  البصمة» الواحدة إلى قيمتين."""
    parts = text.split()
    if len(parts) == 1 and _DEVICE_RE.match(parts[0]):
        return "", parts[0]                    # بصمة وحدها، بلا كود
    if len(parts) == 2 and _DEVICE_RE.match(parts[1]):
        return parts[0], parts[1]
    if len(parts) == 2 and _DEVICE_RE.match(parts[0]):
        return parts[1], parts[0]
    raise SystemExit(
        "تعذّر فهم اللصقة. الصق الكود وبصمة الجهاز معًا كما ينسخهما زرّ "
        "«نسخ» في شاشة التفعيل، أو مرّرهما بعلَمَي --code و --device.")


def main() -> int:
    enable_utf8_console()

    parser = argparse.ArgumentParser(
        description="ينشئ عقد تفعيل بلا إنترنت جاهزًا للّصق والإرسال.")
    parser.add_argument(
        "pasted", nargs="?",
        help="الكود وبصمة الجهاز معًا، كما نسخهما زرّ «نسخ» في شاشة التفعيل.")
    parser.add_argument("--code", default="", help="الكود وحده.")
    parser.add_argument("--device", default="", help="بصمة الجهاز وحدها.")
    parser.add_argument("--days", type=int, help="عدد الأيام (افتراضيًّا 7).")
    parser.add_argument("--max-days", type=int, default=0,
                        help="سقف أيام الاستخدام الفعلية (الافتراضي = --days).")
    parser.add_argument("--plan", default="")
    args = parser.parse_args()

    if args.pasted:
        code, device = _split_pasted(args.pasted.strip())
    elif args.device:
        code, device = args.code.strip(), args.device.strip()
    else:
        pasted = input(
            "الصق الكود وبصمة الجهاز معًا (كما ينسخهما زرّ «نسخ» في شاشة "
            "التفعيل): ").strip()
        code, device = _split_pasted(pasted)

    if not device:
        raise SystemExit("بصمة الجهاز مطلوبة.")

    days = args.days
    if days is None:
        raw = input("كم يومًا؟ [7]: ").strip()
        days = int(raw) if raw else 7

    now = int(time.time())
    lease = Lease(
        code=code or _new_code(), device=device,
        issued_at=now, expires_at=now + days * 86400,
        max_days=args.max_days or days,
        recheck_at=0,                      # عقدٌ بلا إنترنت: لا إعادة تحقّق
        grace_days=0, plan=args.plan or f"{days} يومًا (بلا إنترنت)")
    lease_text = sign(lease, _private_key())

    print()
    print(f"الجهاز:  {device}")
    print(f"الكود:   {lease.code}")
    print(f"المدّة:  {days} يومًا")
    print()
    print("عقدٌ جاهز — انسخ السطر التالي كاملًا وأرسله إلى المعلّم:")
    print()
    print(lease_text)
    print()

    try:
        import pyperclip
        pyperclip.copy(lease_text)
        print("(نُسخ أيضًا إلى الحافظة.)")
    except Exception:
        pass                                # لا حرج: العقد مطبوعٌ أعلاه على كل حال

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
