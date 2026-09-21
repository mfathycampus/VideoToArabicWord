"""تشخيص خادم التفعيل — يطبع ردّ الخادم كما هو، لا رسالةً مُختصرة.

    python tools/ping_license.py            # /admin/status ثم /activate بكودٍ وهمي
    python tools/ping_license.py CODE       # يجرّب تفعيل كودٍ حقيقي على هذا الجهاز

الفائدة أن «403» من حماية الحافة و«403» من منطق الخادم يبدوان سواءً في
نافذة التفعيل؛ هنا يظهر الفرق: نوع المحتوى وجسم الردّ وترويسة cf-ray.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

sys.path.insert(0, ".")

from licensing.fingerprint import device_fingerprint
from licensing.keys import SERVER_URL


def call(path: str, payload: dict | None = None) -> None:
    url = f"{SERVER_URL.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method="POST" if data else "GET",
        headers={"content-type": "application/json",
                 "user-agent": "VideoToArabicWord/diag",
                 "accept": "application/json"})
    print(f"\n=== {request.get_method()} {url}")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status, headers, body = response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        status, headers, body = exc.code, exc.headers, exc.read()
    except Exception as exc:                           # noqa: BLE001
        print(f"تعذّر الاتصال: {exc!r}")
        return
    print("status:", status)
    print("content-type:", headers.get("content-type"))
    print("cf-ray:", headers.get("cf-ray"))
    print("body:", body.decode("utf-8", "replace")[:600])


def main() -> None:
    device = device_fingerprint()
    print("server:", SERVER_URL)
    print("device:", device)
    call("/admin/status")
    code = sys.argv[1].strip().upper() if len(sys.argv) > 1 else "VTAW-0000-0000-0000-0000"
    call("/activate", {"code": code, "device": device, "app_version": "diag"})


if __name__ == "__main__":
    main()
