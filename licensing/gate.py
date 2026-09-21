"""قرار «هل يعمل البرنامج الآن؟» — نقطة واحدة تُستدعى عند الإقلاع.

الترتيب مقصود: التوقيع، ثم الجهاز، ثم الساعة، ثم أيام الاستخدام، ثم
التاريخ، ثم إعادة التحقّق. كل فحصٍ أرخص من الذي بعده وأصعب على التحايل.

**ولماذا أيام الاستخدام قبل تاريخ الانتهاء:** إرجاع الساعة إلى الوراء
يُبقي ``expires_at`` بعيدًا في المستقبل — فلو كانت المدّة تُحسب بفارق
تاريخين لعملت الحيلة. عدّ الأيام المستعملة لا يتأثّر: كل يومٍ يُفتح فيه
البرنامج يُسجَّل مرّة، والرجوع إلى يومٍ مسجَّل لا يضيف شيئًا.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from licensing.lease import Lease, LeaseError
from licensing.signing import verify
from licensing.state import StateStore

#: فرقٌ مسموحٌ به بين ساعة الجهاز وآخر وقتٍ رُئي (ثوانٍ). مزامنة الوقت
#: العادية تُحرّك الساعة دقائق؛ ما فوق ذلك رجوعٌ متعمّد.
CLOCK_TOLERANCE_SECONDS = 15 * 60


class Status(Enum):
    ACTIVE = "active"
    NEEDS_ACTIVATION = "needs_activation"
    EXPIRED = "expired"
    CLOCK_TAMPERED = "clock_tampered"
    DEVICE_MISMATCH = "device_mismatch"
    NEEDS_RECHECK = "needs_recheck"      # يعمل، ومهلة إعادة التحقّق سارية
    BLOCKED = "blocked"                  # انتهت المهلة ولم يتحقّق


@dataclass(frozen=True)
class Verdict:
    status: Status
    message: str
    lease: Optional[Lease] = None
    days_left: int = 0
    days_used: int = 0

    @property
    def can_process(self) -> bool:
        """هل يُسمح ببدء معالجة جديدة؟ فتح المخرجات السابقة يبقى متاحًا."""
        return self.status in (Status.ACTIVE, Status.NEEDS_RECHECK)


def _today(now: float) -> str:
    return datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")


def evaluate(store: StateStore, device: str, now: Optional[float] = None,
             public_key_b64: Optional[str] = None) -> Verdict:
    now = time.time() if now is None else now
    state = store.read()
    text = state.get("lease") or ""
    if not text:
        return Verdict(Status.NEEDS_ACTIVATION,
                       "البرنامج يحتاج كود تفعيل للبدء.")

    try:
        lease = verify(text, public_key_b64)
    except LeaseError as exc:
        return Verdict(Status.NEEDS_ACTIVATION,
                       f"ترخيصٌ غير صالح ({exc}) — أدخل كود تفعيل.")

    if lease.device and lease.device != device:
        return Verdict(Status.DEVICE_MISMATCH,
                       "هذا الترخيص مربوط بجهاز آخر. اطلب كودًا لهذا الجهاز.",
                       lease)

    last_seen = int(state.get("last_seen") or 0)
    if last_seen and now < last_seen - CLOCK_TOLERANCE_SECONDS:
        return Verdict(
            Status.CLOCK_TAMPERED,
            "ساعة الجهاز أُرجِعت إلى الوراء. صحّح التاريخ والوقت، "
            "أو اتصل بالإنترنت ليُعاد التحقّق من الترخيص.", lease)

    days = set(state.get("days") or [])
    days.add(_today(now))
    used = len(days)
    if lease.max_days and used > lease.max_days:
        return Verdict(Status.EXPIRED,
                       f"انتهت مدّة الترخيص ({lease.max_days} يومًا استُعملت).",
                       lease, 0, used)

    if lease.expires_at and now > lease.expires_at:
        return Verdict(Status.EXPIRED, "انتهت صلاحية كود التفعيل.",
                       lease, 0, used)

    left = lease.days_left(now)
    if lease.recheck_at and now > lease.recheck_at:
        deadline = lease.recheck_at + max(0, lease.grace_days) * 86400
        if now > deadline:
            return Verdict(
                Status.BLOCKED,
                "يحتاج البرنامج اتصالًا بالإنترنت مرّة واحدة لتجديد التحقّق.",
                lease, left, used)
        return Verdict(
            Status.NEEDS_RECHECK,
            "يعمل البرنامج، ويحتاج اتصالًا بالإنترنت خلال "
            f"{max(1, int((deadline - now) // 86400))} يومًا لتجديد التحقّق.",
            lease, left, used)

    return Verdict(Status.ACTIVE,
                   (f"الترخيص فعّال — يتبقّى {left} يومًا." if lease.expires_at
                    else "الترخيص فعّال."), lease, left, used)


def record_use(store: StateStore, now: Optional[float] = None,
               lease_text: Optional[str] = None) -> dict:
    """يسجّل «رأيت هذا الوقت، واستُعمل هذا اليوم» في كل المواضع."""
    now = time.time() if now is None else now
    state = store.read()
    days = set(state.get("days") or [])
    days.add(_today(now))
    state.update({
        "last_seen": max(int(state.get("last_seen") or 0), int(now)),
        "days": sorted(days),
        "saved_at": int(now),
    })
    if lease_text:
        state["lease"] = lease_text
    store.write(state)
    return state
