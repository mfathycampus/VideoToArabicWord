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
    headers_out: dict = {}
    get_body: dict = {}

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        RECEIVED["path"] = self.path
        RECEIVED["headers"] = dict(self.headers)
        RECEIVED["body"] = json.loads(self.rfile.read(length))
        self.send_response(Handler.status)
        self.send_header("content-type", "application/json")
        for name, value in Handler.headers_out.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(json.dumps(Handler.body).encode())

    def do_GET(self):
        RECEIVED["path"] = self.path
        RECEIVED["headers"] = dict(self.headers)
        self.send_response(Handler.status)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(Handler.get_body).encode())

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
def test_managed_is_offered_first_then_claude():
    """المُدار أول الخيارات (لا يحتاج مفتاحًا)، ثم Claude بمفتاح المستخدم."""
    assert [k for k, _ in PROVIDER_CHOICES[:2]] == ["maeen_managed", "anthropic"]


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


# ---------------- «معين المُدار» ----------------
@pytest.fixture
def managed(server, monkeypatch):
    """مزوّد مُدار يتجه إلى الخادم الوهمي بكودٍ وجهازٍ معروفين."""
    from licensing import app_gate

    monkeypatch.setenv("VTAW_LICENSE_SERVER", server)
    monkeypatch.setattr(app_gate, "current_code", lambda: "VTAW-AAAA-BBBB")
    monkeypatch.setattr(app_gate, "device_id", lambda: "DEVICE-1")
    RECEIVED.clear()
    Handler.status = 200
    Handler.body = {"content": [{"type": "text", "text": "ok"}]}
    yield build_provider("maeen_managed")
    Handler.status = 200
    Handler.body = {"content": [{"type": "text", "text": '{"title":"عنوان"}'}]}


def test_managed_is_registered_and_cloud():
    from utils.audit import CLOUD_PROVIDERS

    assert "maeen_managed" in [name for name, _ in PROVIDER_CHOICES]
    provider = build_provider("maeen_managed")
    assert provider.info.is_local is False
    assert provider.info.name in CLOUD_PROVIDERS


def test_managed_sends_code_and_device_not_a_key(managed):
    assert managed.complete("sys", "user") == "ok"
    assert RECEIVED["path"] == "/v1/messages"
    sent = {k.lower(): v for k, v in RECEIVED["headers"].items()}
    assert sent["x-api-key"] == "VTAW-AAAA-BBBB"
    assert sent["x-device"] == "DEVICE-1"
    assert "anthropic-workspace-id" not in sent


def test_managed_ignores_local_key_and_workspace(server, monkeypatch):
    """مفتاح Anthropic المحلي لا يُرسل إلى خادم المزوّد أبدًا."""
    from licensing import app_gate

    monkeypatch.setenv("VTAW_LICENSE_SERVER", server)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SECRET")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_x")
    monkeypatch.setattr(app_gate, "current_code", lambda: "VTAW-CODE")
    monkeypatch.setattr(app_gate, "device_id", lambda: "D")
    RECEIVED.clear()
    build_provider("maeen_managed").complete("s", "u")
    sent = json.dumps(RECEIVED["headers"])
    assert "sk-ant-SECRET" not in sent and "wrkspc_x" not in sent


def test_managed_unavailable_without_code(monkeypatch, server):
    from licensing import app_gate

    monkeypatch.setenv("VTAW_LICENSE_SERVER", server)
    monkeypatch.setattr(app_gate, "current_code", lambda: None)
    provider = build_provider("maeen_managed")
    assert provider.is_available() is False
    with pytest.raises(RewriteUnavailableError, match="مُدار"):
        provider.complete("s", "u")


def test_managed_never_falls_back_to_anthropic_host(monkeypatch):
    """بلا خادم تفعيل مضبوط لا يُرسل الكود إلى api.anthropic.com."""
    import licensing.keys as keys
    from licensing import app_gate

    monkeypatch.delenv("VTAW_LICENSE_SERVER", raising=False)
    monkeypatch.setattr(keys, "SERVER_URL", "")
    monkeypatch.setattr(app_gate, "current_code", lambda: "VTAW-CODE")
    provider = build_provider("maeen_managed")
    assert provider.base_url == ""
    assert provider.is_available() is False


def test_managed_insufficient_balance_shows_server_message(managed):
    Handler.status = 402
    Handler.body = {"type": "error", "error": {
        "type": "billing_error", "message": "نفد رصيد الدقائق. اشحن رصيدك"}}
    with pytest.raises(RewriteUnavailableError, match="نفد رصيد") as info:
        managed.complete("s", "u")
    assert info.value.retryable is False
    from ai.providers import CreditExhaustedError

    assert isinstance(info.value, CreditExhaustedError)
    assert info.value.error_code == "E5402"
    assert "التفريغ محفوظ" in str(info.value)


def test_credit_exhaustion_stops_rewriter_not_raw_fallback():
    """نفاد الرصيد يوقف الصياغة بدل إخراج نصّ خام بصمت."""
    from ai.providers import CreditExhaustedError
    from ai.rewriter import RewriteConfig, TranscriptRewriter
    from config.schemas import AudioSegment

    class Broke:
        info = type("I", (), {"name": "maeen_managed", "is_local": False,
                              "supports_images": False})()

        def is_available(self):
            return True

        def complete(self, *a, **k):
            raise CreditExhaustedError("نفد")

    rewriter = TranscriptRewriter(Broke(), RewriteConfig(enabled=True))
    segment = AudioSegment(id=1, start=0, end=5, text_raw="نص تجريبي", text_clean="نص تجريبي")
    with pytest.raises(CreditExhaustedError):
        rewriter._rewrite_batch([segment])


def test_managed_busy_is_retryable(managed):
    Handler.status = 429
    Handler.body = {"type": "error", "error": {
        "type": "rate_limit_error", "message": "طلبات متزامنة كثيرة"}}
    with pytest.raises(RewriteUnavailableError) as info:
        managed.complete("s", "u")
    assert info.value.retryable is True
    assert "متزامنة" in str(info.value)


def test_anthropic_provider_behaviour_unchanged(server, monkeypatch):
    """الخطّافات الجديدة لا تغيّر مزوّد Claude الأصلي."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    RECEIVED.clear()
    AnthropicProvider(base_url=server).complete("s", "u")
    sent = {k.lower(): v for k, v in RECEIVED["headers"].items()}
    assert sent["x-api-key"] == "sk-ant-test"
    assert "x-device" not in sent


# ---------------- عرض الرصيد المتبقي ----------------
def test_managed_captures_credit_header(managed):
    from ai.providers import managed_credit_state

    Handler.headers_out = {"x-maeen-credit-minutes": "42.5"}
    try:
        managed.complete("s", "u")
    finally:
        Handler.headers_out = {}
    state = managed_credit_state()
    assert state["minutes"] == 42.5 and state["at"] > 0


def test_managed_ignores_bad_credit_header(managed):
    import ai.providers as providers

    providers._managed_credit.clear()
    Handler.headers_out = {"x-maeen-credit-minutes": "not-a-number"}
    try:
        managed.complete("s", "u")
    finally:
        Handler.headers_out = {}
    assert providers.managed_credit_state() == {}


def test_plain_anthropic_provider_never_records_credit(server, monkeypatch):
    import ai.providers as providers

    providers._managed_credit.clear()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    Handler.headers_out = {"x-maeen-credit-minutes": "99"}
    try:
        AnthropicProvider(base_url=server).complete("s", "u")
    finally:
        Handler.headers_out = {}
    assert providers.managed_credit_state() == {}


def test_managed_balance_client(server, monkeypatch):
    from licensing import client

    monkeypatch.setenv("VTAW_LICENSE_SERVER", server)
    Handler.get_body = {"credit_minutes": 17.5, "enabled": True}
    RECEIVED.clear()
    info = client.managed_balance("vtaw-aaaa", "DEV-9")
    sent = {k.lower(): v for k, v in RECEIVED["headers"].items()}
    assert info["credit_minutes"] == 17.5
    assert RECEIVED["path"] == "/managed/balance"
    assert sent["x-api-key"] == "VTAW-AAAA" and sent["x-device"] == "DEV-9"


def test_managed_balance_client_shows_server_message(server, monkeypatch):
    from licensing import client

    monkeypatch.setenv("VTAW_LICENSE_SERVER", server)
    Handler.status = 403
    Handler.get_body = {"type": "error", "error": {
        "type": "permission_error", "message": "هذا الجهاز غير مرتبط بالكود."}}
    try:
        with pytest.raises(client.ActivationError, match="غير مرتبط"):
            client.managed_balance("VTAW-X", "D")
    finally:
        Handler.status = 200


def test_managed_rejection_is_fatal_not_swallowed_per_batch(managed):
    """403 (جهاز/كود) يوقف الصياغة بسببها، لا مستند خام موسوم ai:."""
    from ai.providers import FatalProviderError

    Handler.status = 403
    Handler.body = {"type": "error", "error": {
        "type": "permission_error", "message": "هذا الجهاز غير مرتبط بالكود."}}
    with pytest.raises(FatalProviderError, match="غير مرتبط"):
        managed.complete("s", "u")


def test_fatal_error_stops_rewriter_batch():
    from ai.providers import FatalProviderError
    from ai.rewriter import RewriteConfig, TranscriptRewriter
    from config.schemas import AudioSegment

    class Rejected:
        info = type("I", (), {"name": "maeen_managed", "is_local": False,
                              "supports_images": False})()

        def is_available(self):
            return True

        def complete(self, *a, **k):
            raise FatalProviderError("الخدمة المُدارة غير مهيّأة")

    rewriter = TranscriptRewriter(Rejected(), RewriteConfig(enabled=True))
    segment = AudioSegment(id=1, start=0, end=5, text_raw="نص", text_clean="نص")
    with pytest.raises(FatalProviderError):
        rewriter._rewrite_batch([segment])
