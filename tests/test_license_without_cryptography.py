"""التفعيل لا يتوقّف على امتداد cryptography المُترجَم (1.15.1).

رُصد على جهاز معلّمٍ نظيف: المثبِّت 1.15.0 رفض كود التفعيل وطلب
«pip install cryptography». المعلّم لا يعرف ما pip، والحزمة المجمَّدة
لا ترى ما يثبّته أصلًا. التحقّق الآن يسقط إلى تنفيذ Ed25519 خالص.
"""
import builtins
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from licensing import ed25519_pure, signing
from licensing.lease import Lease, LeaseError, decode, encode

DAY = 86400

# RFC 8032 §7.1 — الاختباران 1 و2 و3
VECTORS = [
    ("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f"
     "b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
     "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
     "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]


@pytest.mark.parametrize("public, message, signature", VECTORS)
def test_pure_verifier_accepts_rfc8032_vectors(public, message, signature):
    assert ed25519_pure.verify(bytes.fromhex(public), bytes.fromhex(message),
                               bytes.fromhex(signature))


@pytest.mark.parametrize("public, message, signature", VECTORS)
def test_pure_verifier_rejects_tampering(public, message, signature):
    sig = bytearray.fromhex(signature)
    sig[5] ^= 1
    assert not ed25519_pure.verify(bytes.fromhex(public),
                                   bytes.fromhex(message), bytes(sig))
    assert not ed25519_pure.verify(bytes.fromhex(public),
                                   bytes.fromhex(message) + b"x",
                                   bytes.fromhex(signature))


def test_pure_verifier_rejects_malformed_input():
    assert not ed25519_pure.verify(b"\x00" * 31, b"", b"\x00" * 64)
    assert not ed25519_pure.verify(b"\x00" * 32, b"", b"\x00" * 63)
    # s ≥ L مرفوض (قابلية التطويع — RFC 8032 §5.1.7)
    public, _, signature = VECTORS[0]
    bad = bytes.fromhex(signature)[:32] + b"\xff" * 32
    assert not ed25519_pure.verify(bytes.fromhex(public), b"", bad)


def _lease():
    now = int(time.time())
    return Lease(code="VTAW-TEST", device="dev-1", issued_at=now,
                 expires_at=now + 7 * DAY, max_days=7, recheck_at=now + 7 * DAY,
                 grace_days=3, plan="تجربة")


@pytest.fixture
def no_cryptography(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "cryptography" or name.startswith("cryptography."):
            raise ImportError("DLL load failed while importing _rust")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_activation_works_when_cryptography_cannot_load(no_cryptography):
    """العقد الموقَّع عند المالك يُقبل على جهازٍ لم تتحمّل فيه cryptography."""
    private, public = _keys()
    text = _sign_outside(private)
    lease = signing.verify(text, public)
    assert lease.code == "VTAW-TEST"
    assert signing.last_backend == "pure"


def test_forged_lease_still_rejected_without_cryptography(no_cryptography):
    private, public = _keys()
    lease, signature = decode(_sign_outside(private))
    longer = Lease(**{**lease.__dict__, "expires_at": lease.expires_at + 3650 * DAY})
    with pytest.raises(LeaseError, match="غير صالح"):
        signing.verify(encode(longer, signature), public)


def test_no_user_facing_message_mentions_pip(no_cryptography):
    """لا رسالة تصل المستخدم من مسار التحقّق تذكر pip."""
    _, public = _keys()
    with pytest.raises(LeaseError) as caught:
        signing.verify("VTAW1.AAAA.AAAA", public)
    assert "pip" not in str(caught.value)
    import ast

    tree = ast.parse(Path(signing.__file__).read_text(encoding="utf-8"))
    messages = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not any("ثبّتها عبر" in m for m in messages)


_CACHE = {}


def _keys():
    if "keys" not in _CACHE:
        _CACHE["keys"] = signing.generate_keypair()
    return _CACHE["keys"]


def _sign_outside(private):
    # التوقيع يحتاج cryptography (عند المالك) — يُحسب قبل التزييف ويُخزَّن.
    return _CACHE["signed"]


@pytest.fixture(autouse=True, scope="module")
def _presign():
    private, _ = _keys()
    _CACHE["signed"] = signing.sign(_lease(), private)
    yield


def test_selftest_reports_the_verifier():
    from utils.deps import check_license_verifier

    result = check_license_verifier()
    assert result.ok, result.detail


def test_frozen_hints_never_mention_pip(monkeypatch):
    from utils import bundle

    monkeypatch.setattr(bundle, "is_frozen", lambda: True)
    assert "pip" not in bundle.user_hint("ثبّته عبر: pip install x")
    monkeypatch.setattr(bundle, "is_frozen", lambda: False)
    assert "pip" in bundle.user_hint("ثبّته عبر: pip install x")


def test_build_spec_ships_the_fallback_and_stable_abi_dll():
    spec = (Path(__file__).resolve().parents[1] / "build.spec").read_text(encoding="utf-8")
    assert "licensing.ed25519_pure" in spec
    assert 'collect_submodules("cryptography")' in spec
    assert "python3.dll" in spec
