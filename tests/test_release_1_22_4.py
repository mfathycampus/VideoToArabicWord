"""1.22.4: الدفعة الأولى من تدقيق 12 نوفمبر 2026 — جانب التطبيق.

مهلة القراءة في ``urllib`` ترفع ``TimeoutError`` خامًا لا ``URLError``.
كانت تمرّ من المزوّدين كلهم: لا رسالة مفهومة، ولا إعادة محاولة، ولا سطر في
سجلّ الإرسال — مع أن الطلب أُرسل كاملًا. اختبارات الخادم في
``server/test/worker.test.mjs``.
"""
from __future__ import annotations

import http.client
import json
import urllib.error

import pytest

from ai import providers
from ai.providers import (
    AnthropicProvider,
    MaeenManagedProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    RewriteUnavailableError,
)


@pytest.fixture(autouse=True)
def _clean_egress():
    providers.reset_egress()
    yield
    providers.reset_egress()


def _raising(exc):
    def urlopen(request, timeout=None):
        raise exc
    return urlopen


class _HangsOnRead:
    """ردٌّ وصلت ترويساته ثم انقطع جسمه."""

    headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        raise TimeoutError("timed out")


IMAGE_PROMPT = [providers.text_part("صف اللقطة"),
                providers.image_part("QUJD"), providers.image_part("REVG")]


# ---------------------------------------------------------------------
# المهلة بعد الإرسال: خطأ مفهوم قابل للإعادة + سطر في سجلّ الإرسال
# ---------------------------------------------------------------------
@pytest.mark.parametrize("exc", [
    TimeoutError("timed out"),
    ConnectionResetError("reset by peer"),
    http.client.RemoteDisconnected("closed without response"),
    http.client.IncompleteRead(b""),
])
def test_anthropic_no_response_is_retryable_and_recorded_as_sent(monkeypatch, exc):
    monkeypatch.setattr(providers.urllib.request, "urlopen", _raising(exc))
    provider = AnthropicProvider(api_key="k")
    with pytest.raises(RewriteUnavailableError) as caught:
        provider.complete("s", "u", timeout=7)
    assert caught.value.retryable
    report = providers.egress_report()
    assert report["sent"] is True
    assert provider.info.name in report["providers"]


def test_timeout_message_names_the_wait_and_says_the_text_left(monkeypatch):
    monkeypatch.setattr(providers.urllib.request, "urlopen",
                        _raising(TimeoutError("timed out")))
    with pytest.raises(RewriteUnavailableError) as caught:
        AnthropicProvider(api_key="k").complete("s", "u", timeout=7)
    message = str(caught.value)
    assert "7 ثانية" in message and "أُرسل الطلب" in message
    assert "http" not in message


def test_openai_compatible_timeout_is_retryable_and_recorded(monkeypatch):
    monkeypatch.setattr(providers.urllib.request, "urlopen",
                        _raising(TimeoutError("timed out")))
    provider = OpenAICompatibleProvider(model="m", api_key="k")
    with pytest.raises(RewriteUnavailableError) as caught:
        provider.complete("s", "u")
    assert caught.value.retryable
    assert providers.egress_report()["sent"] is True


def test_ollama_timeout_is_a_readable_error_and_stays_local(monkeypatch):
    monkeypatch.setattr(providers.urllib.request, "urlopen",
                        _raising(TimeoutError("timed out")))
    with pytest.raises(RewriteUnavailableError) as caught:
        OllamaProvider().complete("s", "u")
    assert caught.value.retryable
    assert providers.egress_report()["sent"] is False     # محلي: لا إرسال


@pytest.mark.parametrize("make", [
    lambda: AnthropicProvider(api_key="k"),
    lambda: OpenAICompatibleProvider(model="m", api_key="k"),
])
def test_images_are_counted_once_when_the_body_read_times_out(monkeypatch, make):
    """انقطاعٌ أثناء قراءة الجسم يأتي بعد أن سُجّل الإرسال — لا يُعدّ مرّتين."""
    monkeypatch.setattr(providers.urllib.request, "urlopen",
                        lambda request, timeout=None: _HangsOnRead())
    with pytest.raises(RewriteUnavailableError):
        make().complete("s", IMAGE_PROMPT)
    report = providers.egress_report()
    assert report["sent"] is True and report["images"] == 2


def test_connection_failure_is_still_recorded_as_not_sent(monkeypatch):
    """``URLError`` = لم يُفتح اتصال. السلوك القديم باقٍ: لم يغادر شيء."""
    monkeypatch.setattr(providers.urllib.request, "urlopen",
                        _raising(urllib.error.URLError("no route")))
    with pytest.raises(RewriteUnavailableError):
        AnthropicProvider(api_key="k").complete("s", "u")
    assert providers.egress_report()["sent"] is False


def test_retry_wrapper_retries_a_timeout(monkeypatch):
    calls = []

    class _Ok:
        headers: dict = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"content": [{"type": "text", "text": "تم"}],
                               "stop_reason": "end_turn"}).encode()

    def urlopen(request, timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            raise TimeoutError("timed out")
        return _Ok()

    monkeypatch.setattr(providers.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(providers, "BACKOFF_SECONDS", 0)
    text = providers.complete_with_retry(AnthropicProvider(api_key="k"), "s", "u")
    assert text == "تم" and len(calls) == 2


# ---------------------------------------------------------------------
# «المُدار»: مهلة العميل تتجاوز ميزانية الخادم دائمًا
# ---------------------------------------------------------------------
def _managed(monkeypatch):
    from licensing import app_gate, client

    monkeypatch.setattr(client, "server_url", lambda: "https://maeen.example")
    monkeypatch.setattr(app_gate, "current_code", lambda: "VTAW-AAAA-BBBB-CCCC-DDDD")
    monkeypatch.setattr(app_gate, "device_id", lambda: "0123456789abcdef0123")
    return MaeenManagedProvider()


@pytest.mark.parametrize("asked, expected", [(180, 320), (60, 320), (900, 900)])
def test_managed_waits_longer_than_the_server_budget(monkeypatch, asked, expected):
    seen = []

    def urlopen(request, timeout=None):
        seen.append(timeout)
        raise TimeoutError("timed out")

    monkeypatch.setattr(providers.urllib.request, "urlopen", urlopen)
    with pytest.raises(RewriteUnavailableError):
        _managed(monkeypatch).complete("s", "u", timeout=asked)
    assert seen == [expected]


def test_client_floor_exceeds_the_budget_written_in_the_worker():
    """الرقمان في ملفّين ولغتين: إن افترقا عاد العطل نفسه بصمت."""
    import re
    from pathlib import Path

    worker = (Path(__file__).resolve().parent.parent / "server" / "worker.js"
              ).read_text(encoding="utf-8")
    budget_ms = int(re.search(r"const MANAGED_BUDGET_MS = (\d+);", worker).group(1))
    assert MaeenManagedProvider.MIN_TIMEOUT_SECONDS * 1000 >= budget_ms + 30000
