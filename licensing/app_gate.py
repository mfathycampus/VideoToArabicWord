"""الواجهة التي يستعملها التطبيق: فحصٌ واحد، وتفعيلٌ واحد.

بقية الحزمة تفاصيل. هذا الملف كل ما يحتاجه ``app.py`` والواجهة.
"""
from __future__ import annotations

import time
from typing import Optional

from licensing import client
from licensing.fingerprint import device_fingerprint, legacy_fingerprint
from licensing.gate import Status, Verdict, evaluate, record_use
from licensing.lease import LeaseError
from licensing.signing import verify
from licensing.state import StateStore
from utils.logger import logger

_device: Optional[str] = None
_legacy: Optional[tuple] = None
_store: Optional[StateStore] = None


def machine_ids() -> tuple:
    """كل بصمات هذا الجهاز: الحالية أولًا، ثم ما سبقها (1.22.7).

    عقدٌ صدر لأيٍّ منها عقدٌ لهذا الجهاز. عقدٌ نُسخ من جهاز آخر لا يطابق أيًّا
    منها، فيُرفض كما كان.
    """
    global _device, _legacy
    if _device is None:
        _device = device_fingerprint()
    if _legacy is None:
        try:
            _legacy = (legacy_fingerprint(),)
        except Exception:                              # noqa: BLE001
            _legacy = ()
    return (_device, *(d for d in _legacy if d and d != _device))


def device_id() -> str:
    """البصمة التي يعرف بها الخادم هذا الجهاز الآن.

    هي بصمة العقد المثبَّت إن كانت من بصمات هذا الجهاز (عقدٌ صدر قبل 1.22.7
    ببصمة قديمة، ولم يُجدَّد بعد)، وإلا البصمة الحالية. يُرسَل هذا لا غيره في
    ترويسة «المُدار» والإحصاءات: الخادم لا يعرف الجديدة قبل الانتقال.
    """
    ids = machine_ids()
    try:
        bound = verify(store().read().get("lease") or "").device
    except Exception:                                  # noqa: BLE001
        return ids[0]
    return bound if bound in ids else ids[0]


def _previous_id() -> str:
    """بصمة قديمة لهذا الجهاز ما زال العقد المثبَّت مربوطًا بها، أو ""."""
    bound = device_id()
    return bound if bound != machine_ids()[0] else ""


def _previous_kw() -> dict:
    previous = _previous_id()
    return {"previous": previous} if previous else {}


def store() -> StateStore:
    global _store
    if _store is None:
        ids = machine_ids()
        _store = StateStore(ids[0], legacy_devices=ids[1:])
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


def current_code() -> Optional[str]:
    """كود التفعيل من العقد المحفوظ، أو ``None`` إن لم يُفعَّل شيء.

    يستعمله المزوّد المُدار بدل مفتاح Claude. لا شبكة هنا ولا رمي.
    """
    try:
        lease = verify(store().read().get("lease") or "")
    except Exception:                                  # noqa: BLE001
        return None
    return lease.code or None


def current_tier() -> str:
    """طبقة الترخيص من العقد الموقَّع: ``trial`` أو ``solo`` أو ``team`` أو ``managed``.

    العقود القديمة بلا حقل ``tier`` تُستنتج من اسم الخطة (تبدأ بـ«تجربة»).
    فارغة إن لم يُفعَّل شيء أو في نسخة التطوير. لا شبكة ولا رمي.
    """
    if not licensing_enabled():
        return ""
    try:
        lease = verify(store().read().get("lease") or "")
    except Exception:                                  # noqa: BLE001
        return ""
    return tier_of(lease)


def managed_allowed() -> bool:
    """هل يُعرض «معين المُدار» في الواجهة؟ فقط لكودٍ أُصدر من باقة «مُدار».

    كود «مفتاحك» أو التجربة لا رصيد له عند الخادم: عرض الخيار له يقود
    المستخدم إلى رفضٍ لا يفهم سببه.
    """
    return current_tier() == "managed"


def is_trial() -> bool:
    return current_tier() == "trial"


def refresh_online(app_version: str = "") -> Verdict:
    """يجدّد العقد من الخادم. يُستدعى عند ``NEEDS_RECHECK`` أو بطلب المستخدم."""
    state = store().read()
    text = state.get("lease") or ""
    try:
        lease = verify(text)
    except LeaseError:
        return check(app_version)
    current, previous = machine_ids()[0], _previous_id()
    try:
        renewed = client.recheck(lease.code, current, app_version,
                                 **({"previous": previous} if previous else {}))
    except client.ActivationError as exc:
        if previous and not exc.offline:
            # خادمٌ لا يعرف الانتقال بعد (أقدم من 1.22.7): يُجدَّد بالبصمة
            # القديمة كما كان، ويُعاد الانتقال في التجديد التالي.
            logger.info(f"تعذّر نقل الترخيص إلى البصمة الجديدة: {exc}")
            try:
                return _install(client.recheck(lease.code, previous, app_version),
                                app_version)
            except client.ActivationError as retry:
                exc = retry
        logger.info(f"تعذّر تجديد الترخيص: {exc}")
        return check(app_version)
    return _install(renewed, app_version)


def activate(code: str, app_version: str = "") -> Verdict:
    """تفعيل بكود عبر الخادم."""
    lease_text = client.activate(code, machine_ids()[0], app_version,
                                 **_previous_kw())
    return _install(lease_text, app_version)


def current_lease():
    """العقد المثبَّت الآن (``Lease``) أو ``None``. لا شبكة ولا رمي."""
    try:
        return verify(store().read().get("lease") or "")
    except Exception:                                  # noqa: BLE001
        return None


def fetch_lease(code: str, app_version: str = "") -> str:
    """يفعّل الكود عند الخادم ويعيد العقد **دون تثبيته** — لتغيير الكود.

    الواجهة تقارنه بالحالي أولًا (‏``describe_change``)؛ إن رفض المستخدم
    بقي العقد الحالي كما هو.
    """
    lease_text = client.activate(code, machine_ids()[0], app_version,
                                 **_previous_kw())
    lease = verify(lease_text)
    if lease.device and lease.device not in machine_ids():
        raise LeaseError("هذا الترخيص صادر لجهاز آخر.")
    return lease_text


def install_lease(lease_text: str, app_version: str = "") -> Verdict:
    """يثبّت عقدًا موقَّعًا مكان الحالي — يصير هو المرجع لكل شيء فورًا."""
    return _install(lease_text, app_version)


def tier_of(lease) -> str:
    """طبقة عقدٍ بعينه (انظر ``current_tier``)."""
    extra = getattr(lease, "extra", None) or {}
    plan = getattr(lease, "plan", "") or ""
    tier = str(extra.get("tier") or "")
    if not tier and plan.startswith("تجربة"):
        tier = "trial"
    if not tier and (extra.get("managed") or plan.startswith("مُدار")):
        tier = "managed"
    return tier


def activate_offline(lease_text: str, app_version: str = "") -> Verdict:
    """تفعيل بعقدٍ موقَّع يُلصَق نصًّا — لجهازٍ بلا إنترنت."""
    return _install(lease_text, app_version)


def _install(lease_text: str, app_version: str) -> Verdict:
    lease = verify(lease_text)                      # يرفع LeaseError إن فسد
    if lease.device and lease.device not in machine_ids():
        raise LeaseError("هذا الترخيص صادر لجهاز آخر.")
    record_use(store(), time.time(), lease_text)
    verdict = evaluate(store(), device_id())
    logger.info(f"الترخيص: {verdict.status.value} — {verdict.message}")
    return verdict
