"""إصدار أكواد التفعيل وإدارتها — للمالك وحده.

    python tools/make_license.py init                      # مرّة واحدة: مفتاحان
    python tools/make_license.py new --days 7 --count 5     # أكواد تجربة أسبوع
    python tools/make_license.py new --days 30 --plan "شهر"
    python tools/make_license.py offline --code VTAW-.. --device a1b2.. --days 7
    python tools/make_license.py revoke --code VTAW-....

‏``init`` يولّد زوج مفاتيح: العام يُكتب في ``licensing/keys.py`` ويُشحن مع
البرنامج، والخاص يُحفظ في ``license_private.key`` **خارج المستودع**.
ضياع الخاص يعني أن الأكواد القديمة تبقى صالحة ولا يمكن إصدار جديد؛
تسرّبه يعني أن غيرك يستطيع إصدار تراخيص — احفظه كما تحفظ كلمة مرور.

‏``new`` ينشئ أكوادًا ويرفعها إلى الخادم (إن ضُبط) ليربطها بأول جهاز
يفعّلها. و``offline`` يوقّع عقدًا لجهازٍ بعينه بلا خادم — لمعلّم بلا
إنترنت: يرسل لك بصمة جهازه من شاشة التفعيل، وترسل له العقد نصًّا.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from licensing.lease import Lease  # noqa: E402
from licensing.signing import generate_keypair, sign  # noqa: E402

PRIVATE_KEY_FILE = ROOT / "license_private.key"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # بلا O/0 و I/1


def _new_code() -> str:
    groups = ["".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
              for _ in range(4)]
    return "VTAW-" + "-".join(groups)


def _private_key() -> str:
    if not PRIVATE_KEY_FILE.exists():
        raise SystemExit(f"لا مفتاح خاص في {PRIVATE_KEY_FILE} — شغّل: init")
    return PRIVATE_KEY_FILE.read_text(encoding="utf-8").strip()


def _admin_post(server: str, token: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{server.rstrip('/')}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json",
                 "authorization": f"Bearer {token}"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"رفض الخادم ({exc.code}): "
                         f"{exc.read().decode('utf-8')[:200]}") from exc


def cmd_init(args) -> int:
    if PRIVATE_KEY_FILE.exists() and not args.force:
        raise SystemExit(
            f"يوجد مفتاح خاص في {PRIVATE_KEY_FILE}. توليد مفتاح جديد يُبطل "
            "كل الأكواد الصادرة. أضف --force إن كنت متأكدًا.")
    private, public = generate_keypair()
    PRIVATE_KEY_FILE.write_text(private + "\n", encoding="utf-8")
    keys = ROOT / "licensing" / "keys.py"
    text = keys.read_text(encoding="utf-8")
    text = text.replace('PUBLIC_KEY_B64 = ""', f'PUBLIC_KEY_B64 = "{public}"')
    if args.server:
        text = text.replace('SERVER_URL = ""', f'SERVER_URL = "{args.server}"')
    keys.write_text(text, encoding="utf-8")
    print(f"المفتاح الخاص: {PRIVATE_KEY_FILE}  ← احفظه ولا ترفعه إلى Git")
    print(f"المفتاح العام كُتب في {keys}")
    print("وللخادم: ضَع المفتاح الخاص سرًّا باسم LICENSE_PRIVATE_KEY")
    return 0


def cmd_new(args) -> int:
    codes = [_new_code() for _ in range(args.count)]
    if args.server and args.token:
        for code in codes:
            _admin_post(args.server, args.token, "/admin/codes", {
                "code": code, "days": args.days, "max_days": args.max_days or args.days,
                "recheck_days": args.recheck_days, "grace_days": args.grace_days,
                "plan": args.plan or f"{args.days} يومًا", "note": args.note})
        print(f"رُفع {len(codes)} كودًا إلى الخادم:")
    else:
        print("بلا خادم (سجّلها عندك يدويًّا):")
    for code in codes:
        print(" ", code)
    return 0


def cmd_offline(args) -> int:
    now = int(time.time())
    lease = Lease(
        code=args.code or _new_code(), device=args.device,
        issued_at=now, expires_at=now + args.days * 86400,
        max_days=args.max_days or args.days,
        recheck_at=0,                      # عقدٌ بلا إنترنت: لا إعادة تحقّق
        grace_days=0, plan=args.plan or f"{args.days} يومًا (بلا إنترنت)")
    print(sign(lease, _private_key()))
    return 0


def cmd_revoke(args) -> int:
    if not (args.server and args.token):
        raise SystemExit("الإلغاء يحتاج --server و --token.")
    _admin_post(args.server, args.token, "/admin/revoke", {"code": args.code})
    print(f"أُلغي {args.code}. يتوقّف عند أول إعادة تحقّق.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="إصدار أكواد التفعيل")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="توليد زوج المفاتيح")
    init.add_argument("--server", default="")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    new = subparsers.add_parser("new", help="أكواد جديدة")
    new.add_argument("--days", type=int, required=True)
    new.add_argument("--count", type=int, default=1)
    new.add_argument("--max-days", type=int, default=0,
                     help="سقف أيام الاستخدام الفعلية (الافتراضي = --days)")
    new.add_argument("--recheck-days", type=int, default=7)
    new.add_argument("--grace-days", type=int, default=3)
    new.add_argument("--plan", default="")
    new.add_argument("--note", default="")
    new.add_argument("--server", default="")
    new.add_argument("--token", default="")
    new.set_defaults(func=cmd_new)

    offline = subparsers.add_parser("offline", help="عقد موقَّع لجهاز بعينه")
    offline.add_argument("--device", required=True)
    offline.add_argument("--days", type=int, required=True)
    offline.add_argument("--max-days", type=int, default=0)
    offline.add_argument("--code", default="")
    offline.add_argument("--plan", default="")
    offline.set_defaults(func=cmd_offline)

    revoke = subparsers.add_parser("revoke", help="إلغاء كود")
    revoke.add_argument("--code", required=True)
    revoke.add_argument("--server", default="")
    revoke.add_argument("--token", default="")
    revoke.set_defaults(func=cmd_revoke)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
