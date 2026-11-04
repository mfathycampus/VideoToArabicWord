"""إصدار أكواد التفعيل وإدارتها — للمالك وحده.

    python tools/make_license.py init                      # مرّة واحدة: مفتاحان
    python tools/make_license.py new --days 7 --count 5     # أكواد تجربة أسبوع
    python tools/make_license.py new --days 30 --plan "شهر"
    python tools/make_license.py offline --code VTAW-.. --device a1b2.. --days 7
    python tools/make_license.py revoke --code VTAW-....

‏``init`` يولّد زوج مفاتيح: العام يُكتب في ``licensing/keys.py`` ويُشحن مع
البرنامج، والخاص يُحفظ في ``license_private.key`` **خارج مجلد المشروع**:
‏``%APPDATA%\\VideoToArabicWord\\`` على ويندوز (مجلد المستخدم، لا يُشارَك ولا
يدخل أرشيف المشروع)، أو المسار في ``VTAD_LICENSE_KEY``. المفتاح القديم في جذر
المشروع يعمل مع تحذير، و``move-key`` ينقله.
ضياع الخاص يعني أن الأكواد القديمة تبقى صالحة ولا يمكن إصدار جديد؛
تسرّبه يعني أن غيرك يستطيع إصدار تراخيص — احفظه كما تحفظ كلمة مرور.

‏``new`` ينشئ أكوادًا ويرفعها إلى الخادم (إن ضُبط) ليربطها بأول جهاز
يفعّلها. و``offline`` يوقّع عقدًا لجهازٍ بعينه بلا خادم — لمعلّم بلا
إنترنت: يرسل لك بصمة جهازه من شاشة التفعيل، وترسل له العقد نصًّا.
"""
from __future__ import annotations

import argparse
import json
import os
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
from utils.console import enable_utf8_console  # noqa: E402

KEY_FILENAME = "license_private.key"
#: الموضع القديم — داخل مجلد المشروع، فيُنسخ معه ويُضغط معه (مراجعة 1.20.0).
LEGACY_KEY_FILE = ROOT / KEY_FILENAME


def key_path() -> Path:
    """موضع المفتاح الخاص: ``VTAD_LICENSE_KEY`` أو مجلد إعداد المستخدم."""
    override = os.environ.get("VTAD_LICENSE_KEY", "").strip()
    if override:
        return Path(override)
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "VideoToArabicWord" / KEY_FILENAME
    return Path.home() / ".config" / "videotoarabicword" / KEY_FILENAME


def _write_key(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)          # بلا أثر يُذكر على ويندوز؛ مجلد المستخدم يحميه
    except OSError:
        pass
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # بلا O/0 و I/1


def _new_code() -> str:
    groups = ["".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
              for _ in range(4)]
    return "VTAW-" + "-".join(groups)


def _private_key() -> str:
    path = key_path()
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    if LEGACY_KEY_FILE.exists():
        print(f"⚠ المفتاح الخاص في مجلد المشروع ({LEGACY_KEY_FILE}) — يُنسخ مع "
              "المجلد ويدخل أي أرشيف له. انقله: python tools/make_license.py move-key",
              file=sys.stderr)
        return LEGACY_KEY_FILE.read_text(encoding="utf-8").strip()
    raise SystemExit(f"لا مفتاح خاص في {path} — شغّل: init")


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


def cmd_move_key(args) -> int:
    """ينقل المفتاح من جذر المشروع إلى ``key_path()`` — لا يولّد ولا يغيّر شيئًا."""
    target = key_path()
    if not LEGACY_KEY_FILE.exists():
        print(f"لا مفتاح في مجلد المشروع. الموضع الحالي: {target}"
              + ("" if target.exists() else " (غير موجود)"))
        return 0
    text = LEGACY_KEY_FILE.read_text(encoding="utf-8")
    if target.exists():
        if target.read_text(encoding="utf-8").strip() != text.strip():
            raise SystemExit(f"يوجد مفتاح مختلف في {target} — لم يُنقل شيء.")
    else:
        _write_key(target, text)
    if target.read_text(encoding="utf-8").strip() != text.strip():
        raise SystemExit("تعذّر التحقق من النسخة الجديدة — لم يُحذف الأصل.")
    LEGACY_KEY_FILE.unlink()
    print(f"نُقل المفتاح إلى {target} وحُذف من مجلد المشروع.")
    return 0


def cmd_init(args) -> int:
    existing = next((p for p in (key_path(), LEGACY_KEY_FILE) if p.exists()), None)
    if existing and not args.force:
        raise SystemExit(
            f"يوجد مفتاح خاص في {existing}. توليد مفتاح جديد يُبطل "
            "كل الأكواد الصادرة. أضف --force إن كنت متأكدًا.")
    private, public = generate_keypair()
    _write_key(key_path(), private + "\n")
    keys = ROOT / "licensing" / "keys.py"
    text = keys.read_text(encoding="utf-8")
    text = text.replace('PUBLIC_KEY_B64 = ""', f'PUBLIC_KEY_B64 = "{public}"')
    if args.server:
        text = text.replace('SERVER_URL = ""', f'SERVER_URL = "{args.server}"')
    keys.write_text(text, encoding="utf-8")
    print(f"المفتاح الخاص: {key_path()}  ← احفظ نسخة منه في مكان آمن ولا ترفعه إلى Git")
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
    enable_utf8_console()
    parser = argparse.ArgumentParser(description="إصدار أكواد التفعيل")
    subparsers = parser.add_subparsers(dest="command", required=True)

    move = subparsers.add_parser(
        "move-key", help="نقل المفتاح الخاص من مجلد المشروع إلى مجلد المستخدم")
    move.set_defaults(func=cmd_move_key)

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
