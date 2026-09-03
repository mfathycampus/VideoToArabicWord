"""تخزين محلي مشفَّر للأسرار (رمز HuggingFace ومفاتيح مزوّدي الصياغة).

سياق القرار: تخزين هذه القيم نصًا صريحًا في ``config.yaml`` كان يعني أن
أي نسخة احتياطية، مزامنة سحابية، أداة تفتح الملف للفحص، أو مشاركة غير
مقصودة تكشف المفتاح كاملًا وقابلًا للقراءة مباشرة. الطبقة المفضلة هي ``keyring`` لاستخدام مخزن أسرار نظام التشغيل (Windows
Credential Manager / macOS Keychain / Linux Secret Service). وإذا تعذر
تهيئة backend للنظام، يوجد fallback محلي متوافق مع النسخ السابقة.

**حدود الـfallback:** هذا ليس AES ولا مبنيًّا على مكتبة تشفير مُدقَّقة
مثل ``cryptography``. هو تدفّق مفاتيح من HMAC-SHA256 موثَّق بنمط
Encrypt-then-MAC لكشف العبث. لذلك الـfallback مخصص لمنع الكشف العرضي،
وليس بديلاً عن Credential Manager ضد مهاجم يملك وصولًا كاملًا للجهاز.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from pathlib import Path

_PREFIX = "enc:v1:"
_KEY_FILENAME = ".secret.key"
_KEY_SIZE = 32
_NONCE_SIZE = 16
_MAC_SIZE = 32
_KEYRING_SERVICE = "VideoToArabicWord"
_KEYRING_PREFIX = "secret:"

def _keyring():
    """Return keyring module when available; never make it mandatory at import time."""
    try:
        import keyring  # type: ignore
        return keyring
    except Exception:
        return None

def _keyring_key(config_dir: Path) -> str:
    import hashlib
    return _KEYRING_PREFIX + hashlib.sha256(str(Path(config_dir).resolve()).encode("utf-8")).hexdigest()


def _key_path(config_dir: Path) -> Path:
    return Path(config_dir) / _KEY_FILENAME


def _load_or_create_key(config_dir: Path) -> bytes:
    """Prefer OS credential storage; fall back to the legacy local key file."""
    config_dir = Path(config_dir)
    kr = _keyring()
    if kr is not None:
        try:
            username = _keyring_key(config_dir)
            stored = kr.get_password(_KEYRING_SERVICE, username)
            if stored:
                key = base64.urlsafe_b64decode(stored.encode("ascii"))
                if len(key) == _KEY_SIZE:
                    return key
            key = secrets.token_bytes(_KEY_SIZE)
            kr.set_password(_KEYRING_SERVICE, username, base64.urlsafe_b64encode(key).decode("ascii"))
            return key
        except Exception:
            pass

    path = _key_path(config_dir)
    if path.exists():
        key = path.read_bytes()
        if len(key) == _KEY_SIZE:
            return key
    config_dir.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(_KEY_SIZE)
    path.write_bytes(key)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(4, "big"),
                        hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def _xor(data: bytes, keystream: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, keystream))


def encrypt_field(value: str, config_dir: Path) -> str:
    """يشفّر قيمة نصية للتخزين في ملف الإعداد. سلسلة فارغة تبقى فارغة."""
    if not value:
        return value
    key = _load_or_create_key(config_dir)
    nonce = secrets.token_bytes(_NONCE_SIZE)
    plaintext = value.encode("utf-8")
    ciphertext = _xor(plaintext, _keystream(key, nonce, len(plaintext)))
    mac = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    blob = base64.urlsafe_b64encode(nonce + mac + ciphertext).decode("ascii")
    return _PREFIX + blob


def decrypt_field(value: str, config_dir: Path) -> str:
    """يفك تشفير قيمة محفوظة.

    قيمة قديمة نصًا صريحًا (بلا البادئة ``enc:v1:``) تُعاد كما هي — هذا
    ما يجعل ترقية إعداد قديم شفافة بلا خطوة هجرة صريحة: أول ``save()``
    بعد الترقية يشفّرها تلقائيًا.
    """
    if not value or not value.startswith(_PREFIX):
        return value
    try:
        raw = base64.urlsafe_b64decode(value[len(_PREFIX):].encode("ascii"))
        nonce = raw[:_NONCE_SIZE]
        mac = raw[_NONCE_SIZE:_NONCE_SIZE + _MAC_SIZE]
        ciphertext = raw[_NONCE_SIZE + _MAC_SIZE:]
        keys = []
        kr = _keyring()
        if kr is not None:
            try:
                stored = kr.get_password(_KEYRING_SERVICE, _keyring_key(Path(config_dir)))
                if stored:
                    keys.append(base64.urlsafe_b64decode(stored.encode("ascii")))
            except Exception:
                pass
        key_path = _key_path(config_dir)
        if key_path.exists():
            keys.append(key_path.read_bytes())
        for key in keys:
            if len(key) != _KEY_SIZE:
                continue
            expected = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
            if hmac.compare_digest(mac, expected):
                plaintext = _xor(ciphertext, _keystream(key, nonce, len(ciphertext)))
                return plaintext.decode("utf-8")
        return ""
    except Exception:
        return ""
