"""الاتصال بخادم التفعيل — ``urllib`` وحدها، بلا تبعية جديدة.

الخادم هو مصدر **الوقت** و**الإلغاء**: عقدٌ صالح التوقيع لكنه مُلغى
لا يُجدَّد، وساعة الجهاز لا تُصدَّق في تحديد الانتهاء.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

#: يُضبط عند النشر (``licensing/server_url.py`` أو متغيّر بيئة).
DEFAULT_TIMEOUT = 15


class ActivationError(RuntimeError):
    """رسالةٌ صالحةٌ للعرض على المستخدم كما هي."""

    def __init__(self, message: str, offline: bool = False) -> None:
        super().__init__(message)
        self.offline = offline


def _hint_path():
    import os
    from pathlib import Path

    base = os.environ.get("APPDATA") or os.path.join(
        os.path.expanduser("~"), ".config")
    return Path(base) / "VideoToArabicWord" / "server_hint.json"


def _clean_url(value) -> str:
    value = str(value or "").strip().rstrip("/")
    return value if value.startswith("https://") else ""


def _cached_hints() -> list:
    try:
        data = json.loads(_hint_path().read_text(encoding="utf-8"))
        return [u for u in (_clean_url(x) for x in data.get("servers", [])) if u]
    except Exception:                                  # noqa: BLE001
        return []


def remember_servers(servers) -> None:
    """يحفظ عناوين يرسلها الخادم نفسه، فينتقل العميل إلى عنوان جديد بلا تحديث.

    العنوان لا يُعرض للمستخدم أبدًا؛ والعقد موقَّع فلا يمكن لعنوانٍ مزيَّف
    أن يمنح ترخيصًا.
    """
    try:
        urls = [u for u in (_clean_url(x) for x in (servers or [])) if u][:4]
        if not urls:
            return
        path = _hint_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"servers": urls}), encoding="utf-8")
    except Exception:                                  # noqa: BLE001
        pass


def server_urls() -> list:
    """كل العناوين بالترتيب: بيئة التطوير، فما أرسله الخادم، فالمضمَّن."""
    import os

    from licensing import keys

    ordered = [os.environ.get("VTAW_LICENSE_SERVER"), *_cached_hints(),
               keys.SERVER_URL, *getattr(keys, "FALLBACK_URLS", ())]
    result: list = []
    for item in ordered:
        url = (item or "").strip().rstrip("/")
        if url and url not in result:
            result.append(url)
    return result


def server_url() -> str:
    urls = server_urls()
    return urls[0] if urls else ""


_URL_RE = re.compile(r"https?://\S+")


def _scrub(text) -> str:
    """لا يظهر عنوان الخادم في أي رسالة تصل المستخدم."""
    return _URL_RE.sub("[الخادم]", str(text))


def _network_error(exc) -> str:
    return "تعذّر الاتصال بخادم التفعيل — تحقّق من الإنترنت ثم أعد المحاولة."


def _post(path: str, payload: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    bases = server_urls()
    if not bases:
        raise ActivationError(
            "لا خادم تفعيل مضبوط في هذه النسخة — استعمل التفعيل بلا إنترنت.")
    last: ActivationError = ActivationError("تعذّر الاتصال بخادم التفعيل.", offline=True)
    for base in bases:
        try:
            body = _post_one(base, path, payload, timeout)
        except ActivationError as exc:
            if not exc.offline:
                raise                       # رفضٌ مفهوم من الخادم: لا فائدة من بديل
            last = exc
            continue
        remember_servers(body.get("servers"))
        return body
    raise last


def _post_one(base: str, path: str, payload: dict, timeout: int) -> dict:
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
        raise ActivationError(_scrub(detail or f"رفض الخادم الطلب ({exc.code}).")) from exc
    except Exception as exc:                           # noqa: BLE001 — شبكة
        raise ActivationError(_network_error(exc), offline=True) from exc
    if not body.get("lease"):
        raise ActivationError(_scrub(body.get("error") or "ردٌّ غير مفهوم من الخادم."))
    return body


def _get(path: str, code: str, device: str, timeout: int) -> dict:
    bases = server_urls()
    if not bases:
        raise ActivationError("لا خادم مضبوط في هذه النسخة.")
    last = ActivationError("تعذّر الاتصال بالخادم.", offline=True)
    for base in bases:
        request = urllib.request.Request(
            f"{base}{path}",
            headers={"x-api-key": code.strip().upper(), "x-device": device,
                     "user-agent": "VideoToArabicWord/balance",
                     "accept": "application/json"}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            message = ""
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                error = payload.get("error")
                message = error.get("message") if isinstance(error, dict) else str(error or "")
            except Exception:                          # noqa: BLE001
                pass
            raise ActivationError(
                _scrub(message or f"رفض الخادم الطلب ({exc.code}).")) from exc
        except Exception as exc:                       # noqa: BLE001
            last = ActivationError(_network_error(exc), offline=True)
    raise last


def managed_balance(code: str, device: str,
                    timeout: int = DEFAULT_TIMEOUT) -> dict:
    """رصيد كودٍ من باقة «مُدار» (دقائق) دون استهلاك. للعرض في الواجهة."""
    return _get("/managed/balance", code, device, timeout)


def managed_usage(code: str, device: str,
                  timeout: int = DEFAULT_TIMEOUT) -> dict:
    """سجل استهلاك الكود (آخر العمليات) — للعميل نفسه فقط."""
    return _get("/managed/usage", code, device, timeout)


def activate(code: str, device: str, app_version: str = "") -> str:
    """يُفعّل كودًا ويعيد نصّ العقد الموقَّع."""
    return _post("/activate", {"code": code.strip().upper(), "device": device,
                               "app_version": app_version})["lease"]


def recheck(code: str, device: str, app_version: str = "") -> str:
    """يجدّد عقدًا قائمًا — يفشل إن أُلغي الكود."""
    return _post("/recheck", {"code": code.strip().upper(), "device": device,
                              "app_version": app_version})["lease"]
