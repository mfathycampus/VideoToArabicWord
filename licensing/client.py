"""الاتصال بخادم التفعيل — ``urllib`` وحدها، بلا تبعية جديدة.

الخادم هو مصدر **الوقت** و**الإلغاء**: عقدٌ صالح التوقيع لكنه مُلغى
لا يُجدَّد، وساعة الجهاز لا تُصدَّق في تحديد الانتهاء.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Optional

#: يُضبط عند النشر (``licensing/server_url.py`` أو متغيّر بيئة).
DEFAULT_TIMEOUT = 15


class ActivationError(RuntimeError):
    """رسالةٌ صالحةٌ للعرض على المستخدم كما هي."""

    def __init__(self, message: str, offline: bool = False) -> None:
        super().__init__(message)
        self.offline = offline


def server_url() -> str:
    import os

    from licensing.keys import SERVER_URL

    return (os.environ.get("VTAW_LICENSE_SERVER") or SERVER_URL or "").rstrip("/")


def _post(path: str, payload: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    base = server_url()
    if not base:
        raise ActivationError(
            "لا خادم تفعيل مضبوط في هذه النسخة — استعمل التفعيل بلا إنترنت.")
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json",
                 # بلا هذا يُرسل urllib ترويسةً قد توقفها حماية الروبوتات
                 # فيعود 403 من الحافة لا من الخادم — رفضٌ بلا رسالة.
                 "user-agent": f"VideoToArabicWord/{payload.get('app_version') or '?'}",
                 "accept": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:              # ردّ الخادم برفضٍ مفهوم
        raw = b""
        try:
            raw = exc.read()
        except Exception:                              # noqa: BLE001
            pass
        detail = None
        try:
            detail = json.loads(raw.decode("utf-8")).get("error")
        except Exception:                              # noqa: BLE001
            # ليس ردّنا: صفحة حجبٍ من الحافة غالبًا. اعرض طرفًا منها
            # بدل «رفض الخادم» المجرّدة التي لا تُشخّص شيئًا.
            text = re.sub(r"<[^>]+>", " ", raw.decode("utf-8", "replace"))
            text = " ".join(text.split())[:160]
            if text:
                detail = f"رفض الخادم الطلب ({exc.code}): {text}"
        raise ActivationError(detail or f"رفض الخادم الطلب ({exc.code}).") from exc
    except Exception as exc:                           # noqa: BLE001 — شبكة
        raise ActivationError(
            f"تعذّر الاتصال بخادم التفعيل: {exc}", offline=True) from exc
    if not body.get("lease"):
        raise ActivationError(body.get("error") or "ردٌّ غير مفهوم من الخادم.")
    return body


def activate(code: str, device: str, app_version: str = "") -> str:
    """يُفعّل كودًا ويعيد نصّ العقد الموقَّع."""
    return _post("/activate", {"code": code.strip().upper(), "device": device,
                               "app_version": app_version})["lease"]


def recheck(code: str, device: str, app_version: str = "") -> str:
    """يجدّد عقدًا قائمًا — يفشل إن أُلغي الكود."""
    return _post("/recheck", {"code": code.strip().upper(), "device": device,
                              "app_version": app_version})["lease"]
