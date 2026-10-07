"""عنوان الخادم: لا يظهر للمستخدم، وينتقل العميل إلى بديل تلقائيًا."""
import json
import urllib.error

import pytest

from licensing import client


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("VTAW_LICENSE_SERVER", raising=False)
    monkeypatch.setattr("licensing.keys.SERVER_URL", "https://primary.example")
    monkeypatch.setattr("licensing.keys.FALLBACK_URLS", ("https://backup.example",),
                        raising=False)


class _Resp:
    def __init__(self, body):
        self._b = json.dumps(body).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_order_env_then_hint_then_builtin(monkeypatch):
    client.remember_servers(["https://hint.example/"])
    monkeypatch.setenv("VTAW_LICENSE_SERVER", "https://dev.example")
    assert client.server_urls() == [
        "https://dev.example", "https://hint.example",
        "https://primary.example", "https://backup.example"]


def test_non_https_hints_are_ignored():
    client.remember_servers(["http://evil.example", "ftp://x"])
    assert client.server_urls()[0] == "https://primary.example"


def test_falls_back_when_primary_unreachable(monkeypatch):
    seen = []

    def fake(request, timeout=0):
        seen.append(request.full_url)
        if "primary" in request.full_url:
            raise urllib.error.URLError("down")
        return _Resp({"lease": "L", "servers": ["https://new.example"]})

    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert client.activate("VTAW-AAAA", "dev") == "L"
    assert seen[0].startswith("https://primary.example")
    assert seen[1].startswith("https://backup.example")
    assert client.server_urls()[0] == "https://new.example"   # حُفظ التلميح


def test_error_messages_never_contain_the_url(monkeypatch):
    def boom(request, timeout=0):
        raise urllib.error.URLError("failed https://primary.example/activate")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(client.ActivationError) as info:
        client.activate("VTAW-AAAA", "dev")
    assert "example" not in str(info.value) and "http" not in str(info.value)

    def refused(request, timeout=0):
        raise urllib.error.HTTPError(
            request.full_url, 403, "x", {},
            __import__("io").BytesIO(json.dumps(
                {"error": "راجع https://primary.example/help"}).encode()))

    monkeypatch.setattr("urllib.request.urlopen", refused)
    with pytest.raises(client.ActivationError) as info:
        client.activate("VTAW-AAAA", "dev")
    assert "example" not in str(info.value)
