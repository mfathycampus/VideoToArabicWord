"""حفظ الإعداد ومفتاح الوصول.

انحدار على تجربة حقيقية: المستخدم ضبط المفتاح بـ ``setx`` مرّتين وظلّ
البرنامج يقول «غير مهيّأ» لأن ``setx`` لا يؤثر على البرامج المفتوحة.
الحل: حقل في الواجهة يحفظ في ملف الإعداد، ويُقرأ قبل متغيّر البيئة.
"""
import os
from pathlib import Path

import pytest

from ai.providers import AnthropicProvider, OllamaProvider, build_provider
from config.settings import AppConfig, default_config_path


def test_config_path_is_stable_and_absolute():
    path = default_config_path()
    assert path.is_absolute()
    assert path.name == "config.yaml"


def test_api_key_survives_save_and_load(tmp_path):
    path = tmp_path / "config.yaml"
    config = AppConfig()
    config.rewrite.enabled = True
    config.rewrite.provider = "anthropic"
    config.rewrite.api_key = "sk-ant-secret"
    config.save(path)

    loaded = AppConfig.load(path)
    assert loaded.rewrite.api_key == "sk-ant-secret"
    assert loaded.rewrite.enabled is True
    assert loaded.rewrite.provider == "anthropic"


def test_config_key_wins_over_environment(monkeypatch):
    """ملف الإعداد يسبق متغيّر البيئة — يعمل فورًا بلا إعادة تشغيل."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    provider = AnthropicProvider(api_key="from-config")
    assert provider.api_key == "from-config"


def test_environment_used_when_config_empty(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert AnthropicProvider(api_key="").api_key == "from-env"


def test_whitespace_only_key_is_not_accepted(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert AnthropicProvider(api_key="   ").is_available() is False


def test_build_provider_forwards_key():
    provider = build_provider("anthropic", api_key="sk-x")
    assert provider.is_available() is True


def test_local_provider_needs_no_key():
    provider = build_provider("ollama")
    assert isinstance(provider, OllamaProvider)
    assert provider.info.is_local is True


def test_rewrite_disabled_by_default():
    """ADR-016: لا اتصال خارجي دون تفعيل صريح."""
    assert AppConfig().rewrite.enabled is False


def test_saved_config_is_valid_yaml(tmp_path):
    import yaml
    path = tmp_path / "c.yaml"
    AppConfig().save(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "rewrite" in data and "document" in data and "whisper" in data


def test_load_missing_file_returns_defaults(tmp_path):
    config = AppConfig.load(tmp_path / "absent.yaml")
    assert config.rewrite.enabled is False
    assert config.whisper.model_size == "large-v3-turbo"


def test_paths_round_trip_as_strings(tmp_path):
    """Path داخل الإعداد يجب أن يُحفَظ نصًا ويُقرأ Path."""
    path = tmp_path / "c.yaml"
    config = AppConfig()
    config.application.output_dir = tmp_path / "out"
    config.save(path)
    loaded = AppConfig.load(path)
    assert Path(loaded.application.output_dir) == tmp_path / "out"
