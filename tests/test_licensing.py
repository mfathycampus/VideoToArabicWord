"""الترخيص: التوقيع، وربط الجهاز، والتلاعب بالساعة، وعدّ الأيام."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from licensing.gate import Status, evaluate, record_use
from licensing.lease import Lease, LeaseError, decode, encode
from licensing.signing import generate_keypair, sign, verify
from licensing.state import StateStore

DAY = 86400


@pytest.fixture(scope="module")
def keys():
    return generate_keypair()


@pytest.fixture
def store(tmp_path):
    return StateStore("dev-1", [tmp_path / "a.json", tmp_path / "b.json"],
                      use_registry=False)


def _lease(now, **overrides):
    base = dict(code="VTAW-TEST", device="dev-1", issued_at=int(now),
                expires_at=int(now + 7 * DAY), max_days=7,
                recheck_at=int(now + 7 * DAY), grace_days=3, plan="تجربة")
    base.update(overrides)
    return Lease(**base)


# ── التوقيع ───────────────────────────────────────────────────────────
def test_a_valid_lease_verifies(keys):
    private, public = keys
    now = time.time()
    assert verify(sign(_lease(now), private), public).code == "VTAW-TEST"


def test_a_forged_lease_is_rejected(keys):
    """تمديد المدّة بتحرير الحمولة يُفسد التوقيع."""
    private, public = keys
    now = time.time()
    lease, signature = decode(sign(_lease(now), private))
    longer = Lease(**{**lease.__dict__, "expires_at": int(now + 3650 * DAY)})
    with pytest.raises(LeaseError):
        verify(encode(longer, signature), public)


def test_a_lease_signed_by_another_key_is_rejected(keys):
    _private, public = keys
    other_private, _ = generate_keypair()
    with pytest.raises(LeaseError):
        verify(sign(_lease(time.time()), other_private), public)


# ── الحالة على الجهاز ─────────────────────────────────────────────────
def test_state_survives_deleting_one_location(store, tmp_path, keys):
    private, public = keys
    now = time.time()
    record_use(store, now, sign(_lease(now), private))
    (tmp_path / "a.json").unlink()
    assert evaluate(store, "dev-1", now, public).status is Status.ACTIVE


def test_hand_edited_state_is_ignored(store, tmp_path, keys):
    private, public = keys
    now = time.time()
    record_use(store, now, sign(_lease(now), private))
    for name in ("a.json", "b.json"):
        (tmp_path / name).write_text(
            json.dumps({"d": {"last_seen": 0, "days": []}, "t": "00"}),
            encoding="utf-8")
    assert evaluate(store, "dev-1", now, public).status is Status.NEEDS_ACTIVATION


def test_state_from_another_device_does_not_apply(tmp_path, keys):
    private, public = keys
    now = time.time()
    mine = StateStore("dev-1", [tmp_path / "a.json"], use_registry=False)
    record_use(mine, now, sign(_lease(now), private))
    theirs = StateStore("dev-2", [tmp_path / "a.json"], use_registry=False)
    assert evaluate(theirs, "dev-2", now, public).status is Status.NEEDS_ACTIVATION


# ── التحايل ───────────────────────────────────────────────────────────
def test_turning_the_clock_back_is_caught(store, keys):
    private, public = keys
    now = time.time()
    record_use(store, now, sign(_lease(now), private))
    verdict = evaluate(store, "dev-1", now - 3 * DAY, public)
    assert verdict.status is Status.CLOCK_TAMPERED
    assert not verdict.can_process


def test_small_clock_corrections_are_tolerated(store, keys):
    """مزامنة الوقت تُحرّك الساعة دقائق — ليست تحايلًا."""
    private, public = keys
    now = time.time()
    record_use(store, now, sign(_lease(now), private))
    assert evaluate(store, "dev-1", now - 300, public).status is Status.ACTIVE


def test_used_days_expire_the_licence_even_if_dates_are_wide(store, keys):
    """سبعة أيام استُعملت = انتهاء، ولو بقي تاريخ الانتهاء بعيدًا."""
    private, public = keys
    now = time.time()
    text = sign(_lease(now, expires_at=int(now + 3650 * DAY),
                       recheck_at=int(now + 3650 * DAY)), private)
    for day in range(8):
        record_use(store, now + day * DAY, text)
    verdict = evaluate(store, "dev-1", now + 7 * DAY, public)
    assert verdict.status is Status.EXPIRED


def test_a_lease_for_another_device_is_refused(store, keys):
    private, public = keys
    now = time.time()
    record_use(store, now, sign(_lease(now, device="dev-9"), private))
    assert evaluate(store, "dev-1", now, public).status is Status.DEVICE_MISMATCH


# ── إعادة التحقّق والمهلة ─────────────────────────────────────────────
def test_recheck_window_keeps_working_then_blocks(store, keys):
    private, public = keys
    now = time.time()
    text = sign(_lease(now, recheck_at=int(now + DAY), grace_days=3,
                       expires_at=int(now + 30 * DAY), max_days=30), private)
    record_use(store, now, text)

    within = evaluate(store, "dev-1", now + 2 * DAY, public)
    assert within.status is Status.NEEDS_RECHECK and within.can_process

    after = evaluate(store, "dev-1", now + 5 * DAY, public)
    assert after.status is Status.BLOCKED and not after.can_process


def test_expiry_blocks_processing_only(store, keys):
    private, public = keys
    now = time.time()
    record_use(store, now, sign(_lease(now), private))
    verdict = evaluate(store, "dev-1", now + 8 * DAY, public)
    assert verdict.status is Status.EXPIRED
    assert not verdict.can_process
    assert verdict.lease is not None        # يبقى معروفًا للعرض والدعم
