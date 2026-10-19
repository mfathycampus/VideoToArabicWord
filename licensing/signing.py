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
    """مكتبة ``cryptography`` للتوقيع وتوليد المفاتيح — عند المالك وحده."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
        return ed25519, serialization
    except Exception as exc:                          # noqa: BLE001
        # هذا المسار لا يبلغه المستخدم: التوقيع والتوليد أدوات المالك.
        raise LeaseError(
            "مكتبة cryptography غير متاحة — أدوات إصدار الترخيص تحتاجها "
            "على جهاز المطوّر.") from exc


#: آخر مسار تحقّق استُعمل — ``cryptography`` أو ``pure``. للتشخيص وحده.
last_backend = ""


def _verify_signature(public: bytes, signature: bytes, payload: bytes) -> bool:
    """يتحقّق بـ ``cryptography`` إن أمكن، وإلا بالتنفيذ الخالص.

    رُصد: حزمةٌ مجمَّدة فشل فيها تحميل ``cryptography`` على جهاز نظيف،
    فطلب البرنامج من المعلّم «pip install cryptography». التحقّق لا يجوز
    أن يتوقّف على امتدادٍ مُترجَم — انظر ``licensing/ed25519_pure``.
    """
    global last_backend
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except Exception as exc:                          # noqa: BLE001
        from utils.logger import logger

        logger.warning(f"cryptography غير متاحة ({exc}) — تحقّق Ed25519 الخالص.")
        ed25519 = None
    if ed25519 is not None:
        last_backend = "cryptography"
        try:
            ed25519.Ed25519PublicKey.from_public_bytes(public).verify(
                signature, payload)
            return True
        except Exception:                             # noqa: BLE001
            return False
    from licensing import ed25519_pure

    last_backend = "pure"
    return ed25519_pure.verify(public, payload, signature)


def sign(lease: Lease, private_key_b64: str) -> str:
    """يوقّع عقدًا ويعيده نصًّا. للمالك وحده."""
    ed25519, _ = _ed25519()
    raw = base64.b64decode(private_key_b64)
    key = ed25519.Ed25519PrivateKey.from_private_bytes(raw)
    return encode(lease, key.sign(lease.to_payload()))


def verify(text: str, public_key_b64: Optional[str] = None) -> Lease:
    """يتحقّق من التوقيع ويعيد العقد، أو يرفع ``LeaseError``."""
    from licensing.keys import PUBLIC_KEY_B64

    public = public_key_b64 or PUBLIC_KEY_B64
    if not public:
        raise LeaseError("لا مفتاح عام مضمَّن — هذه نسخة تطوير غير موقَّعة.")
    lease, signature = decode(text)
    if not _verify_signature(base64.b64decode(public), signature,
                             lease.to_payload()):
        raise LeaseError("توقيع الترخيص غير صالح.")
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
