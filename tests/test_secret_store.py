"""انحدار على تخزين الأسرار: كانت hf_token وapi_key تُحفظان نصًا صريحًا
في config.yaml. الحل تشفير محلي بمفتاح مولَّد على الجهاز (بلا تبعية
جديدة — انظر utils/secret_store.py)، شفّاف تمامًا للكود الذي يقرأ
``config.whisper.hf_token`` أو ``config.rewrite.api_key`` أثناء التشغيل.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import AppConfig
from utils import secret_store
from utils.secret_store import decrypt_field, encrypt_field


def test_encrypted_value_is_not_the_plaintext(tmp_path):
    encrypted = encrypt_field("hf_super_secret_token", tmp_path)
    assert encrypted != "hf_super_secret_token"
    assert "hf_super_secret_token" not in encrypted
    assert encrypted.startswith("enc:v1:")


def test_encrypt_then_decrypt_round_trips(tmp_path):
    encrypted = encrypt_field("sk-ant-api03-abcdef", tmp_path)
    assert decrypt_field(encrypted, tmp_path) == "sk-ant-api03-abcdef"


def test_empty_value_stays_empty(tmp_path):
    assert encrypt_field("", tmp_path) == ""
    assert decrypt_field("", tmp_path) == ""


def test_legacy_plaintext_value_passes_through_unchanged(tmp_path):
    """قيمة من نسخة أقدم (بلا بادئة enc:v1:) يجب أن تُقرأ كما هي —
    هذا ما يجعل ترقية إعداد قديم شفافة بلا خطوة هجرة يدوية."""
    assert decrypt_field("hf_old_plaintext_token", tmp_path) == "hf_old_plaintext_token"


def test_tampered_ciphertext_fails_closed_not_open(tmp_path):
    """عبث بالقيمة المشفّرة يجب أن يُعيد فارغًا لا نصًا مموَّهًا خاطئًا."""
    encrypted = encrypt_field("real-secret", tmp_path)
    tampered = encrypted[:-4] + ("A" if encrypted[-4] != "A" else "B") + encrypted[-3:]
    assert decrypt_field(tampered, tmp_path) == ""


@pytest.fixture
def no_keyring(monkeypatch):
    """يُجبر مسار الاحتياط الملفّي.

    ``_load_or_create_key`` يفضّل مخزن أسرار النظام ولا يكتب ملف مفتاح
    إطلاقًا حين ينجح. على لينكس في CI لا يوجد backend فيقع الاحتياط
    تلقائيًا وتمرّ الاختبارات؛ على **ويندوز** يعمل Credential Manager
    فلا يُنشأ الملف — فسقطت ثلاثة اختبارات تفترض وجوده. الافتراض كان
    خطأ الاختبار لا خطأ الكود: هذه الاختبارات تخصّ الاحتياط، فعليها أن
    تطلبه صراحةً بدل أن تتّكل على غياب backend في بيئة بعينها.
    """
    monkeypatch.setattr(secret_store, "_keyring", lambda: None)


def test_missing_key_file_fails_closed(no_keyring, tmp_path):
    encrypted = encrypt_field("real-secret", tmp_path)
    (tmp_path / ".secret.key").unlink()
    assert decrypt_field(encrypted, tmp_path) == ""


def test_key_file_is_created_with_restrictive_permissions(no_keyring, tmp_path):
    import os
    import stat as statmod

    encrypt_field("x", tmp_path)
    key_path = tmp_path / ".secret.key"
    assert key_path.exists()
    if os.name != "nt":
        mode = statmod.S_IMODE(key_path.stat().st_mode)
        assert mode == 0o600


def test_two_secrets_in_same_dir_share_one_key_file(no_keyring, tmp_path):
    encrypt_field("first-secret", tmp_path)
    key_bytes_after_first = (tmp_path / ".secret.key").read_bytes()
    encrypt_field("second-secret", tmp_path)
    key_bytes_after_second = (tmp_path / ".secret.key").read_bytes()
    assert key_bytes_after_first == key_bytes_after_second


def test_fallback_round_trips_without_any_keyring(no_keyring, tmp_path):
    """الاحتياط وحده كافٍ: تشفير وفك على جهاز بلا مخزن أسرار."""
    encrypted = encrypt_field("sk-fallback-only", tmp_path)
    assert decrypt_field(encrypted, tmp_path) == "sk-fallback-only"


def test_os_credential_store_is_preferred_over_a_key_file(monkeypatch, tmp_path):
    """المسار المفضَّل — وهو ما يعمل فعلًا على ويندوز — كان بلا تغطية.

    حين ينجح مخزن النظام يجب ألّا يُكتب مفتاح على القرص إطلاقًا: وجود
    الملف يعني تسريب المفتاح إلى نسخة احتياطية أو مزامنة سحابية، وهو
    بالضبط ما وُجد المخزن لتفاديه.
    """
    vault: dict[tuple[str, str], str] = {}

    class FakeKeyring:
        @staticmethod
        def get_password(service, username):
            return vault.get((service, username))

        @staticmethod
        def set_password(service, username, password):
            vault[(service, username)] = password

    monkeypatch.setattr(secret_store, "_keyring", lambda: FakeKeyring)

    encrypted = encrypt_field("sk-in-os-vault", tmp_path)
    assert decrypt_field(encrypted, tmp_path) == "sk-in-os-vault"
    assert vault, "المفتاح لم يصل إلى مخزن النظام"
    assert not (tmp_path / ".secret.key").exists(), (
        "كُتب مفتاح على القرص رغم نجاح مخزن النظام")


def test_app_config_hf_token_and_api_key_are_not_plaintext_on_disk(tmp_path):
    path = tmp_path / "config.yaml"
    config = AppConfig()
    config.whisper.hf_token = "hf_plaintext_should_not_appear"
    config.rewrite.api_key = "sk-plaintext_should_not_appear"
    config.save(path)

    raw = path.read_text(encoding="utf-8")
    assert "hf_plaintext_should_not_appear" not in raw
    assert "sk-plaintext_should_not_appear" not in raw

    loaded = AppConfig.load(path)
    assert loaded.whisper.hf_token == "hf_plaintext_should_not_appear"
    assert loaded.rewrite.api_key == "sk-plaintext_should_not_appear"


def test_app_config_loading_legacy_plaintext_config_still_works(tmp_path):
    """إعداد من نسخة أقدم (قبل هذا الإصلاح) يُقرأ بلا أي خطوة يدوية،
    ويُشفَّر تلقائيًا عند أول حفظ لاحق."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "whisper:\n  hf_token: hf_legacy_plaintext\n"
        "rewrite:\n  api_key: sk-legacy-plaintext\n",
        encoding="utf-8")

    loaded = AppConfig.load(path)
    assert loaded.whisper.hf_token == "hf_legacy_plaintext"
    assert loaded.rewrite.api_key == "sk-legacy-plaintext"

    loaded.save(path)
    raw = path.read_text(encoding="utf-8")
    assert "hf_legacy_plaintext" not in raw
    assert "sk-legacy-plaintext" not in raw


def test_app_config_survives_missing_key_file_after_manual_config_copy(tmp_path):
    """نسخ config.yaml وحده (بلا .secret.key المرافق) إلى مكان آخر يجب
    ألا يُسقط الإعداد كله — الحقل السرّي فقط يعود فارغًا."""
    path = tmp_path / "config.yaml"
    config = AppConfig()
    config.whisper.hf_token = "hf_secret"
    config.save(path)

    other_dir = tmp_path / "copied_elsewhere"
    other_dir.mkdir()
    other_path = other_dir / "config.yaml"
    other_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    loaded = AppConfig.load(other_path)
    assert loaded.whisper.hf_token == ""
    assert loaded.load_error == ""  # لا ينبغي أن يُعامَل هذا كفشل تحميل كامل
