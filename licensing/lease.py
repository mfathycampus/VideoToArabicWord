"""عقد الاستخدام (Lease): ما يوقّعه المالك ويتحقّق منه البرنامج.

الشكل: ``VTAW1.<payload-b64url>.<signature-b64url>`` — نصّ واحد يُخزَّن
ويُنقل، ويحمل كل ما يحتاجه الفحص دون اتصال.

الحقول:
    code        الكود الذي فُعِّل به (للإلغاء والدعم)
    device      بصمة الجهاز المربوط، أو "" لعقد غير مربوط
    issued_at   وقت الإصدار من **الخادم** (ثوانٍ UTC)
    expires_at  نهاية الصلاحية (ثوانٍ UTC)
    max_days    سقف أيام الاستخدام الفعلية (0 = بلا سقف)
    recheck_at  بعده يجب إعادة التحقّق عبر الإنترنت (ثوانٍ UTC)
    grace_days  مهلة العمل إن تعذّر الاتصال بعد ``recheck_at``
    plan        وصفٌ للعرض («تجربة أسبوع»)

لماذا التوقيع لا التشفير: البرنامج يحتاج أن **يتحقّق**، لا أن يخفي.
والتشفير بمفتاح مضمَّن في البرنامج وهمٌ أمني: المفتاح يُستخرج منه.
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Optional

PREFIX = "VTAW1"


class LeaseError(ValueError):
    """عقد غير صالح: شكلًا أو توقيعًا."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


@dataclass(frozen=True)
class Lease:
    code: str
    device: str = ""
    issued_at: int = 0
    expires_at: int = 0
    max_days: int = 0
    recheck_at: int = 0
    grace_days: int = 3
    plan: str = ""
    extra: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    def to_payload(self) -> bytes:
        data = {
            "code": self.code, "device": self.device,
            "issued_at": int(self.issued_at), "expires_at": int(self.expires_at),
            "max_days": int(self.max_days), "recheck_at": int(self.recheck_at),
            "grace_days": int(self.grace_days), "plan": self.plan,
        }
        data.update(self.extra or {})
        # ``sort_keys`` شرطٌ للتوقيع: ترتيبٌ مختلف = بايتاتٌ مختلفة = توقيع لا يطابق
        return json.dumps(data, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_payload(cls, raw: bytes) -> "Lease":
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:                       # noqa: BLE001
            raise LeaseError(f"حمولة عقدٍ غير مقروءة: {exc}") from exc
        if not isinstance(data, dict) or not data.get("code"):
            raise LeaseError("حمولة عقدٍ ناقصة.")
        known = {"code", "device", "issued_at", "expires_at", "max_days",
                 "recheck_at", "grace_days", "plan"}
        return cls(
            code=str(data["code"]), device=str(data.get("device") or ""),
            issued_at=int(data.get("issued_at") or 0),
            expires_at=int(data.get("expires_at") or 0),
            max_days=int(data.get("max_days") or 0),
            recheck_at=int(data.get("recheck_at") or 0),
            grace_days=int(data.get("grace_days") or 3),
            plan=str(data.get("plan") or ""),
            extra={k: v for k, v in data.items() if k not in known})

    # ------------------------------------------------------------------
    def days_left(self, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        if not self.expires_at:
            return 10 ** 6
        return max(0, int((self.expires_at - now) // 86400) + (
            1 if (self.expires_at - now) % 86400 else 0))


def encode(lease: Lease, signature: bytes) -> str:
    return f"{PREFIX}.{_b64e(lease.to_payload())}.{_b64e(signature)}"


def decode(text: str) -> tuple[Lease, bytes]:
    parts = (text or "").strip().split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise LeaseError("صيغة العقد غير معروفة.")
    try:
        payload, signature = _b64d(parts[1]), _b64d(parts[2])
    except Exception as exc:                           # noqa: BLE001
        raise LeaseError(f"ترميز العقد غير سليم: {exc}") from exc
    return Lease.from_payload(payload), signature
