"""مزوّدو إعادة الصياغة — شكل الطلب والرد ومعالجة الأخطاء.

يُختبر مقابل خادم وهمي محلي: لا مفاتيح حقيقية ولا اتصال خارجي.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from ai.providers import (
    PROVIDER_CHOICES, AnthropicProvider, OllamaProvider,
    OpenAICompatibleProvider, RewriteUnavailableError, build_provider,
)

RECEIVED = {}


class Handler(BaseHTTPRequestHandler):
    status = 200
    body = {"content": [{"type": "text", "text": '{"title":"عنوان"}'}]}

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        RECEIVED["path"] = self.path
        RECEIVED["headers"] = dict(self.headers)
        RECEIVED["body"] = json.loads(self.rfile.read(length))
        self.send_response(Handler.status)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(Handler.body).encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


# ---------------- التسجيل ----------------
def test_claude_is_offered_first():
    """المستخدم لديه مفتاح Claude — يجب أن يكون الخيار الأول."""
    assert PROVIDER_CHOICES[0][0] == "anthropic"


def test_all_registered_providers_build():
    for name, _ in PROVIDER_CHOICES:
        provider = build_provider(name)
        assert provider.info.name
        assert provider.info.privacy_note


def test_unknown_provider_rejected():
    with pytest.raises(RewriteUnavailableError):
        build_provider("does-not-exist")


def test_local_provider_is_marked_local():
    assert build_provider("ollama").info.is_local is True
    assert build_provider("anthropic").info.is_local is False


# ---------------- شكل طلب Anthropic ----------------
def test_anthropic_request_shape(server, monkeypatch):
    """واجهة Claude تختلف عن OpenAI: x-api-key و system حقل مستقل."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    Handler.status = 200
    provider = AnthropicProvider(base_url=server)
    text = provider.complete("تعليمات النظام", "النص الخام", max_tokens=500)

    assert text == '{"title":"عنوان"}'
    assert RECEIVED["path"] == "/v1/messages"
    headers = {k.lower(): v for k, v in RECEIVED["headers"].items()}
    assert headers["x-api-key"] == "sk-ant-test"
    assert headers["anthropic-version"] == AnthropicProvider.API_VERSION
    assert "authorization" not in headers, "Claude لا يستخدم Authorization"

    body = RECEIVED["body"]
    assert body["system"] == "تعليمات النظام", "system حقل مستقل لا رسالة"
    assert body["messages"] == [{"role": "user", "content": "النص الخام"}]
    assert body["max_tokens"] == 500
    assert body["model"] == AnthropicProvider.DEFAULT_MODEL


def test_anthropic_joins_multiple_text_blocks(server, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    Handler.status = 200
    Handler.body = {"content": [{"type": "text", "text": "جزء١ "},
                                {"type": "text", "text": "جزء٢"}]}
    try:
        assert AnthropicProvider(base_url=server).complete("s", "u") == "جزء١ جزء٢"
    finally:
        Handler.body = {"content": [{"type": "text",
                                     "text": '{"title":"عنوان"}'}]}


def test_anthropic_without_key_gives_actionable_message(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = AnthropicProvider()
    assert provider.is_available() is False
    with pytest.raises(RewriteUnavailableError) as info:
        provider.complete("s", "u")
    message = str(info.value)
    assert "ANTHROPIC_API_KEY" in message
    assert "setx" in message, "الرسالة يجب أن تعطي الأمر الفعلي"


@pytest.mark.parametrize("code,marker", [
    (401, "المفتاح"), (429, "حد الاستخدام"), (404, "غير متاح"),
])
def test_anthropic_http_errors_are_translated(server, monkeypatch, code, marker):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    Handler.status = code
    try:
        with pytest.raises(RewriteUnavailableError) as info:
            AnthropicProvider(base_url=server).complete("s", "u")
        assert marker in str(info.value)
    finally:
        Handler.status = 200


def test_availability_check_makes_no_request(monkeypatch):
    """فحص الجاهزية يجب ألا يستهلك رصيدًا ولا يرسل نصًا."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    RECEIVED.clear()
    assert AnthropicProvider().is_available() is True
    assert RECEIVED == {}


# ---------------- OpenAI المتوافق ----------------
def test_openai_uses_authorization_header(server, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    Handler.status = 200
    Handler.body = {"choices": [{"message": {"content": "نتيجة"}}]}
    try:
        provider = OpenAICompatibleProvider(base_url=server)
        assert provider.complete("s", "u") == "نتيجة"
        headers = {k.lower(): v for k, v in RECEIVED["headers"].items()}
        assert headers["authorization"] == "Bearer sk-x"
        # OpenAI يضع system كرسالة، بعكس Claude
        assert RECEIVED["body"]["messages"][0]["role"] == "system"
    finally:
        Handler.body = {"content": [{"type": "text",
                                     "text": '{"title":"عنوان"}'}]}


def test_workspace_id_header_sent_when_provided(server, monkeypatch):
    """مفتاح مرتبط بهوية يحتاج ترويسة anthropic-workspace-id."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    Handler.status = 200
    AnthropicProvider(base_url=server, workspace_id="wrkspc_abc").complete("s", "u")
    headers = {k.lower(): v for k, v in RECEIVED["headers"].items()}
    assert headers["anthropic-workspace-id"] == "wrkspc_abc"


def test_workspace_header_absent_when_not_configured(server, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)
    Handler.status = 200
    AnthropicProvider(base_url=server).complete("s", "u")
    headers = {k.lower(): v for k, v in RECEIVED["headers"].items()}
    assert "anthropic-workspace-id" not in headers


def test_workspace_id_read_from_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_env")
    assert AnthropicProvider().workspace_id == "wrkspc_env"


def test_missing_workspace_error_is_explained(server, monkeypatch):
    """400 بسبب مساحة العمل يجب أن يشرح الحل لا أن يعرض JSON خامًا.

    الرسالة الأصلية إنجليزية تقنية:
    «anthropic-workspace-id is required when authenticating with an
    identity-linked API key».
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    Handler.status = 400
    Handler.body = {"type": "error", "error": {
        "type": "invalid_request_error",
        "message": "anthropic-workspace-id is required when authenticating "
                   "with an identity-linked API key; send the id of the "
                   "workspace this request acts in."}}
    try:
        with pytest.raises(RewriteUnavailableError) as info:
            AnthropicProvider(base_url=server).complete("s", "u")
        message = str(info.value)
        assert "مساحة العمل" in message
        assert "wrkspc_" in message
    finally:
        Handler.status = 200
        Handler.body = {"content": [{"type": "text", "text": '{"title":"عنوان"}'}]}


def test_temperature_not_sent_by_default(server, monkeypatch):
    """انحدار: النماذج الأحدث ترفض `temperature` بـ 400.

    «`temperature` is deprecated for this model» — الافتراضي الآن ألا
    تُرسل إطلاقًا.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    Handler.status = 200
    AnthropicProvider(base_url=server).complete("s", "u")
    assert "temperature" not in RECEIVED["body"]
    assert "top_p" not in RECEIVED["body"]


def test_temperature_sent_when_explicitly_requested(server, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    Handler.status = 200
    AnthropicProvider(base_url=server, temperature=0.3).complete("s", "u")
    assert RECEIVED["body"]["temperature"] == 0.3


def test_deprecated_param_is_dropped_and_retried(monkeypatch):
    """رفض وسيط مهمَل يجب أن يُسقطه ويعيد المحاولة، لا أن يفشل."""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    calls = {"n": 0, "bodies": []}

    class Retry(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("content-length", 0))
            body = _json.loads(self.rfile.read(length))
            calls["n"] += 1
            calls["bodies"].append(body)
            if "temperature" in body:
                payload = _json.dumps({"type": "error", "error": {
                    "type": "invalid_request_error",
                    "message": "`temperature` is deprecated for this model."}})
                self.send_response(400)
            else:
                payload = _json.dumps(
                    {"content": [{"type": "text", "text": "تم"}]})
                self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(payload.encode())

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Retry)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    try:
        provider = AnthropicProvider(base_url=url, temperature=0.2)
        assert provider.complete("s", "u") == "تم"
        assert calls["n"] == 2, "لم تُعَد المحاولة"
        assert "temperature" in calls["bodies"][0]
        assert "temperature" not in calls["bodies"][1]
    finally:
        httpd.shutdown()
