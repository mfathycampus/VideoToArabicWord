"""توقيع العقود والتحقّق منها — Ed25519.

المفتاح **الخاص** يبقى عند المالك وحده (``tools/make_license.py``)،
والبرنامج يحمل **العام** فقط. فلو فُكّ البرنامج كاملًا لم يستطع أحد
تصنيع عقدٍ صالح: التحقّق لا يكشف ما يُصنّع به.

والبديل الذي تجنّبناه: ``hmac`` بسرٍّ مشترك مضمَّن في البرنامج — أبسط
تنفيذًا، ويسقط عند أول من يستخرج السرّ من الملف التنفيذي، فيصير قادرًا
على إصدار أكواد مدى الحياة.
"""
from __future__ import annotations

import base64
from typing import Optional

from licensing.lease import Lease, LeaseError, decode, encode


def _ed25519():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
        return ed25519, serialization
    except ImportError as exc:                        # noqa: BLE001
        raise LeaseError(
            "حزمة cryptography غير مثبَّتة — لا يمكن التحقّق من الترخيص. "
            "ثبّتها عبر: pip install cryptography") from exc


def sign(lease: Lease, private_key_b64: str) -> str:
    """يوقّع عقدًا ويعيده نصًّا. للمالك وحده."""
    ed25519, _ = _ed25519()
    raw = base64.b64decode(private_key_b64)
    key = ed25519.Ed25519PrivateKey.from_private_bytes(raw)
    return encode(lease, key.sign(lease.to_payload()))


def verify(text: str, public_key_b64: Optional[str] = None) -> Lease:
    """يتحقّق من التوقيع ويعيد العقد، أو يرفع ``LeaseError``."""
    from licensing.keys import PUBLIC_KEY_B64

    ed25519, _ = _ed25519()
    public = public_key_b64 or PUBLIC_KEY_B64
    if not public:
        raise LeaseError("لا مفتاح عام مضمَّن — هذه نسخة تطوير غير موقَّعة.")
    lease, signature = decode(text)
    key = ed25519.Ed25519PublicKey.from_public_bytes(base64.b64decode(public))
    try:
        key.verify(signature, lease.to_payload())
    except Exception as exc:                          # noqa: BLE001
        raise LeaseError("توقيع الترخيص غير صالح.") from exc
    return lease


def generate_keypair() -> tuple[str, str]:
    """(خاص، عام) بترميز base64. يُنفَّذ مرّة واحدة عند المالك."""
    ed25519, serialization = _ed25519()
    private = ed25519.Ed25519PrivateKey.generate()
    raw_private = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption())
    raw_public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    return (base64.b64encode(raw_private).decode(),
            base64.b64encode(raw_public).decode())
