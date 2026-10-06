"""يرفع مفتاح Claude المحفوظ في التطبيق إلى Cloudflare كسرٍّ للخدمة المُدارة.

المفتاح لا يُطبع ولا يُكتب في ملف ولا يظهر في سجلّ الأوامر: يُقرأ من إعداد
التطبيق (أو من متغيّر البيئة) ويمرّ إلى wrangler عبر stdin فقط.

الاستعمال (من مجلد المشروع، وبيئة التطبيق مفعّلة):

    python tools/push_managed_key.py --dry-run   # يعرض ما سيحدث بلا رفع
    python tools/push_managed_key.py             # يرفع بعد تأكيدك

يتطلب مرة واحدة:  cd server  ثم  npx wrangler login
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "server"
sys.path.insert(0, str(ROOT))


def masked(value: str) -> str:
    return f"{value[:7]}...{value[-4:]}" if len(value) > 12 else "..."


def read_credentials() -> tuple[str, str]:
    """(key, workspace id) from the app settings, then the environment."""
    key = workspace = ""
    try:
        from config.settings import AppConfig, default_config_path

        config = AppConfig.load(default_config_path())
        key = (config.rewrite.api_key or "").strip()
        workspace = (config.rewrite.workspace_id or "").strip()
    except Exception as exc:                          # noqa: BLE001
        print(f"Could not read app settings ({exc}); trying environment variables.")
    key = key or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    workspace = workspace or os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    return key, workspace


def put_secret(npx: str, name: str, value: str) -> None:
    result = subprocess.run(
        [npx, "wrangler", "secret", "put", name], cwd=SERVER_DIR,
        input=value + "\n", text=True, capture_output=True)
    if result.returncode != 0:
        # The value goes through stdin only and is never printed.
        print((result.stderr or result.stdout).strip()[-600:])
        sys.exit(f"FAILED to upload {name}. If you are not logged in: "
                 "cd server  then  npx wrangler login")
    print(f"OK: uploaded {name}")


def fetch_status() -> dict | None:
    from licensing.client import server_url

    base = server_url()
    if not base:
        return None
    request = urllib.request.Request(
        f"{base}/admin/status", headers={"user-agent": "push-managed-key"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:                                 # noqa: BLE001
        return None


def wait_for_key(seconds: int = 60) -> None:
    """A new secret takes a few seconds to reach every edge; poll for it."""
    print("Waiting for the server to see the key", end="", flush=True)
    deadline = time.time() + seconds
    status = None
    while time.time() < deadline:
        status = fetch_status()
        if status and status.get("anthropic_key_set"):
            print("\nOK: the server now has the key. Managed service is ready.")
            print(status)
            return
        print(".", end="", flush=True)
        time.sleep(4)
    print("\nNOT visible yet. Open /admin/status in the browser in a minute;")
    print("if anthropic_key_set is still false, run this script again.")
    print(status)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be uploaded (masked), upload nothing")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation")
    args = parser.parse_args()

    key, workspace = read_credentials()
    if not key:
        sys.exit("No Claude key found in the app settings or ANTHROPIC_API_KEY.")
    if not key.startswith("sk-ant-"):
        sys.exit("The value found does not look like an Anthropic key (sk-ant-...).")

    print(f"Key:          {masked(key)}")
    print(f"Workspace id: {workspace or '(not used)'}")
    print("Target:       secret ANTHROPIC_API_KEY on the Worker in server/")
    if args.dry_run:
        print("(dry run: nothing uploaded)")
        return
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        sys.exit("npx not found. Install Node.js first (nodejs.org).")
    if not args.yes and input("Upload this key to Cloudflare? [y/N] "
                              ).strip().lower() not in ("y", "yes"):
        sys.exit("Cancelled.")

    put_secret(npx, "ANTHROPIC_API_KEY", key)
    if workspace:
        put_secret(npx, "ANTHROPIC_WORKSPACE_ID", workspace)
    wait_for_key()


if __name__ == "__main__":
    main()
