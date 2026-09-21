"""الواجهة التي يستعملها التطبيق: فحصٌ واحد، وتفعيلٌ واحد.

بقية الحزمة تفاصيل. هذا الملف كل ما يحتاجه ``app.py`` والواجهة.
"""
from __future__ import annotations

import time
from typing import Optional

from licensing import client
from licensing.fingerprint import device_fingerprint
from licensing.gate import Status, Verdict, evaluate, record_use
from licensing.lease import LeaseError
from licensing.signing import verify
from licensing.state import StateStore
from utils.logger import logger

_device: Optional[str] = None
_store: Optional[StateStore] = None


def device_id() -> str:
    global _device
    if _device is None:
        _device = device_fingerprint()
    return _device


def store() -> StateStore:
    global _store
    if _store is None:
        _store = StateStore(device_id())
    return _store


def licensing_enabled() -> bool:
    """نسخة التطوير بلا مفتاح عام مضمَّن لا تُقفل نفسها."""
    from licensing.keys import PUBLIC_KEY_B64

    return bool(PUBLIC_KEY_B64)


def check(app_version: str = "", now: Optional[float] = None) -> Verdict:
    """الحالة الآن، مع تسجيل «رأيت هذا الوقت» في كل المواضع."""
    if not licensing_enabled():
        return Verdict(Status.ACTIVE, "نسخة تطوير بلا ترخيص.", None, 10 ** 6, 0)
    verdict = evaluate(store(), device_id(), now)
    # يُسجَّل دائمًا — حتى قبل التفعيل: وقتٌ لم يُسجَّل لا يكشف إرجاع الساعة.
    record_use(store(), now)
    return verdict


def refresh_online(app_version: str = "") -> Verdict:
    """يجدّد العقد من الخادم. يُستدعى عند ``NEEDS_RECHECK`` أو بطلب المستخدم."""
    state = store().read()
    text = state.get("lease") or ""
    try:
        lease = verify(text)
    except LeaseError:
        return check(app_version)
    try:
        renewed = client.recheck(lease.code, device_id(), app_version)
    except client.ActivationError as exc:
        logger.info(f"تعذّر تجديد الترخيص: {exc}")
        return check(app_version)
    return _install(renewed, app_version)


def activate(code: str, app_version: str = "") -> Verdict:
    """تفعيل بكود عبر الخادم."""
    lease_text = client.activate(code, device_id(), app_version)
    return _install(lease_text, app_version)


def activate_offline(lease_text: str, app_version: str = "") -> Verdict:
    """تفعيل بعقدٍ موقَّع يُلصَق نصًّا — لجهازٍ بلا إنترنت."""
    return _install(lease_text, app_version)


def _install(lease_text: str, app_version: str) -> Verdict:
    lease = verify(lease_text)                      # يرفع LeaseError إن فسد
    if lease.device and lease.device != device_id():
        raise LeaseError("هذا الترخيص صادر لجهاز آخر.")
    record_use(store(), time.time(), lease_text)
    verdict = evaluate(store(), device_id())
    logger.info(f"الترخيص: {verdict.status.value} — {verdict.message}")
    return verdict
