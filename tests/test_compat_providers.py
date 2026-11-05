"""1.21.0: مزوّدٌ بمفتاح الجهة (OpenAI/Gemini/Azure…) من الواجهة وحدها."""
import io
import json
import os
import urllib.error

import pytest

from ai import providers
from ai.providers import (
    AzureOpenAIProvider,
    OpenAICompatibleProvider,
    build_provider,
    guess_preset,
    provider_from_settings,
)
from config.settings import AppConfig


class _Response:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


OK = {"choices": [{"message": {"content": "تم"}, "finish_reason": "stop"}]}


def test_azure_builds_deployment_url_and_api_key_header(monkeypatch):
    seen = {}

    def urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = {k.lower(): v for k, v in request.header_items()}
        seen["body"] = json.loads(request.data)
        return _Response(OK)

    monkeypatch.setattr(providers.urllib.request, "urlopen", urlopen)
    provider = AzureOpenAIProvider(endpoint="https://res.openai.azure.com/",
                                   deployment="doc-writer", api_key="k")
    assert provider.complete("s", "u") == "تم"
    assert seen["url"] == ("https://res.openai.azure.com/openai/deployments/"
                           "doc-writer/chat/completions?api-version=2024-10-21")
    assert seen["headers"]["api-key"] == "k" and "authorization" not in seen["headers"]
    assert "model" not in seen["body"]
    assert provider.info.name in __import__("utils.audit", fromlist=["x"]).CLOUD_PROVIDERS


def test_new_models_get_max_completion_tokens_after_one_rejection(monkeypatch):
    bodies = []

    def urlopen(request, timeout):
        body = json.loads(request.data)
        bodies.append(body)
        if "max_tokens" in body:
            raise urllib.error.HTTPError(
                request.full_url, 400, "bad", {},
                io.BytesIO(b"Unsupported parameter: 'max_tokens'. Use "
                           b"'max_completion_tokens' instead."))
        return _Response(OK)

    monkeypatch.setattr(providers.urllib.request, "urlopen", urlopen)
    provider = OpenAICompatibleProvider(model="new-model", api_key="k")
    assert provider.complete("s", "u", max_tokens=100) == "تم"
    assert bodies[-1]["max_completion_tokens"] == 100 and "temperature" not in bodies[-1]
    provider.complete("s", "u")                       # يُتذكَّر: طلبٌ واحد
    assert len(bodies) == 3


def test_text_only_model_is_not_given_images():
    from ai.visual_author import VisualAuthor

    config = AppConfig()
    config.rewrite.provider = "openai_compatible"
    config.rewrite.base_url = "https://api.deepseek.com"
    config.rewrite.model = "deepseek-chat"
    config.rewrite.api_key = "k"
    config.rewrite.model_supports_images = False
    provider = provider_from_settings(config.rewrite)
    assert provider.is_available()
    assert not VisualAuthor.usable(provider, [object()])


def test_non_openai_server_needs_an_explicit_model():
    """إرسال gpt-4o-mini إلى Gemini يعطي «نموذج غير موجود» لا «اكتب الاسم»."""
    gemini = build_provider("openai_compatible",
                            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                            api_key="k")
    assert gemini.model == "" and not gemini.is_available()
    assert build_provider("openai_compatible", api_key="k").model == "gpt-4o-mini"


def test_saved_base_url_maps_back_to_its_preset():
    assert guess_preset("") == "openai"
    assert guess_preset("https://api.groq.com/openai/v1/") == "groq"
    assert guess_preset("https://llm.uni.edu.sa/v1") == "custom"


def test_profile_keys_are_encrypted_on_disk(tmp_path):
    config = AppConfig()
    config.rewrite.saved_profiles = {"openai_compatible": {"api_key": "AIzaSECRET"}}
    path = tmp_path / "config.yaml"
    config.save(path)
    assert "AIzaSECRET" not in path.read_text(encoding="utf-8")
    assert AppConfig.load(path).rewrite.saved_profiles[
        "openai_compatible"]["api_key"] == "AIzaSECRET"


# ── الواجهة ──────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PyQt6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    from ui.main_window import MainWindow
    win = MainWindow(AppConfig(), tmp_path / "c.yaml")
    win.rewrite_check.setChecked(True)
    yield win
    win.close()


def _pick(win, name):
    win.provider_combo.setCurrentIndex(win.provider_combo.findData(name))


def test_everything_is_set_from_the_window(window):
    _pick(window, "openai_compatible")
    assert not window.compat_box.isHidden()
    window.preset_combo.setCurrentIndex(window.preset_combo.findData("gemini"))
    assert "generativelanguage" in window.config.rewrite.base_url
    assert window.base_url_input.isReadOnly()
    window.api_key_input.setText("AIzaKEY")
    assert window._missing_compat_fields() == ["اسم النموذج"]
    window.model_input.setText("gemini-model")
    assert window._missing_compat_fields() == []

    _pick(window, "anthropic")                        # لا يرث عنوان Gemini ولا مفتاحه
    assert window.config.rewrite.base_url == "" and window.config.rewrite.api_key == ""
    assert window.compat_box.isHidden()
    _pick(window, "openai_compatible")                # يعود كما كان
    assert window.config.rewrite.model == "gemini-model"
    assert window.config.rewrite.api_key == "AIzaKEY"
    assert window.preset_combo.currentData() == "gemini"


def test_azure_fields(window):
    _pick(window, "azure_openai")
    assert window.preset_combo.isHidden() and not window.api_version_input.isHidden()
    window.base_url_input.setText("https://res.openai.azure.com")
    window.model_input.setText("dep")
    window.api_key_input.setText("k")
    provider = provider_from_settings(window.config.rewrite)
    assert isinstance(provider, AzureOpenAIProvider) and provider.is_available()
