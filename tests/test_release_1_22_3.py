"""1.22.3: ما صحّ من تدقيق 11 نوفمبر 2026 على النسخة الحالية."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------
# SEC-04: لا مفتاح ولا نصّ عبر http:// إلى خادم خارجي
# ---------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1",
    "https://my-resource.openai.azure.com",
    "http://127.0.0.1:11434/v1",
    "http://localhost:8787",
    "http://[::1]:8000/v1",
])
def test_secure_or_loopback_endpoints_pass(url):
    from ai.providers import require_secure_endpoint

    require_secure_endpoint(url)


@pytest.mark.parametrize("url", [
    "http://api.example.com/v1",
    "http://192.168.1.20:8000/v1",
    "https://user:secret@api.example.com/v1",
    "ftp://api.example.com",
    "api.example.com/v1",
    "",
])
def test_plain_http_or_credentials_in_url_are_refused(url):
    from ai.providers import FatalProviderError, require_secure_endpoint

    with pytest.raises(FatalProviderError):
        require_secure_endpoint(url)


def test_openai_compatible_refuses_http_before_any_request(monkeypatch):
    import urllib.request

    from ai.providers import FatalProviderError, OpenAICompatibleProvider

    def no_network(*_a, **_k):
        raise AssertionError("أُرسل طلبٌ رغم أن العنوان بلا تشفير")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    provider = OpenAICompatibleProvider(model="m", base_url="http://api.example.com/v1",
                                        api_key="sk-test")
    with pytest.raises(FatalProviderError):
        provider._send("system", "user", 100, 5)


def test_anthropic_refuses_http_before_any_request(monkeypatch):
    import urllib.request

    from ai.providers import AnthropicProvider, FatalProviderError

    def no_network(*_a, **_k):
        raise AssertionError("أُرسل طلبٌ رغم أن العنوان بلا تشفير")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    provider = AnthropicProvider(model="m", base_url="http://proxy.example.com",
                                 api_key="sk-ant-test")
    with pytest.raises(FatalProviderError):
        provider._post({"messages": []}, {}, 5)


# ---------------------------------------------------------------------
# PRIV-03 (البقية): إشعار الموافقة لكل مهمّة يذكر الاستثناء
# ---------------------------------------------------------------------
def _consent_config(send_unredacted: bool):
    return SimpleNamespace(
        rewrite=SimpleNamespace(send_images=True,
                                send_unredacted_images=send_unredacted),
        document=SimpleNamespace(anonymize_names=True,
                                 withhold_sensitive_screens=True))


def test_consent_names_the_unredacted_images_exception():
    from utils.egress_notice import describe_egress

    text = describe_egress(_consent_config(True), "Claude API")
    assert "send_unredacted_images" in text
    assert "كما هي" in text


def test_consent_is_unchanged_without_the_exception():
    from utils.egress_notice import describe_egress

    assert "send_unredacted_images" not in describe_egress(
        _consent_config(False), "Claude API")


# ---------------------------------------------------------------------
# فشل صيغة اختارها المستخدم يصل إليه
# ---------------------------------------------------------------------
def test_write_exports_reports_failed_formats(tmp_path, monkeypatch):
    import document.exporters as exporters
    from tests.test_exporters import _metadata, _plan

    def boom(_ctx):
        raise RuntimeError("عطل مفتعل")

    exporters._load_builtins()
    monkeypatch.setitem(exporters._REGISTRY, "pdf", boom)
    ctx = exporters.ExportContext(plan=_plan(), metadata=_metadata(tmp_path),
                                  images_dir=tmp_path, base_path=tmp_path / "a.docx")
    failures: list = []
    written = exporters.write_exports(ctx, ["pdf", "chapters"], failures=failures)
    assert failures == ["pdf"]
    assert [p.suffix for p in written] == [".txt"]


def test_pipeline_tells_the_user_which_export_failed(tmp_path, monkeypatch):
    import document.exporters as exporters
    from config.settings import AppConfig
    from core.pipeline_outputs import OutputsMixin
    from tests.test_exporters import _metadata, _plan

    def fake_write(ctx, formats, failures=None):
        failures.append("pptx")
        return []

    monkeypatch.setattr(exporters, "write_exports", fake_write)

    class Host(OutputsMixin):
        def __init__(self):
            self.config = AppConfig()
            self.config.document.export_formats = ["pptx"]
            self.last_notes = ["privacy: ملاحظة سابقة"]

    host = Host()
    host._write_exports(_plan(), _metadata(tmp_path), tmp_path, tmp_path / "a.docx", None)
    assert host.last_notes[0] == "privacy: ملاحظة سابقة"
    assert any("PPTX" in n and "المستند كُتب كاملًا" in n for n in host.last_notes)


def test_translation_failure_reaches_the_user(tmp_path, monkeypatch):
    import ai.providers as providers
    import ai.translator as translator
    from config.settings import AppConfig
    from core.pipeline_outputs import OutputsMixin

    class Ready:
        def is_available(self):
            return True

    monkeypatch.setattr(providers, "provider_from_settings", lambda s: Ready())

    def boom(*_a, **_k):
        raise RuntimeError("انقطع الاتصال")

    monkeypatch.setattr(translator, "translate_subtitles", boom)

    class Host(OutputsMixin):
        def __init__(self):
            self.config = AppConfig()
            self.config.document.translate_to = ["en", "fr"]

    host = Host()
    transcript = SimpleNamespace(segments=[object()])
    host._write_translations(transcript, tmp_path / "a.docx", None)
    assert any("en" in n and "fr" in n for n in host.last_notes)


# ---------------------------------------------------------------------
# PERF-01: المساحة تُفحص على قرص الإخراج نفسه
# ---------------------------------------------------------------------
def test_free_space_is_checked_on_the_nearest_existing_output_folder(tmp_path):
    from core.pipeline_media import _nearest_existing

    target = tmp_path / "جديد" / "أعمق" / "مخرجات"
    assert _nearest_existing(target) == tmp_path.absolute()
    assert _nearest_existing(tmp_path) == tmp_path.absolute()


# ---------------------------------------------------------------------
# SEC-05: الاستئناف لا يقرأ JSON من خارج مجلد المهمة
# ---------------------------------------------------------------------
def _job(tmp_path):
    from core.job_state import JobManager

    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    return JobManager.create_or_resume(tmp_path / "job", video)


def test_load_artifact_reads_inside_the_job_folder(tmp_path):
    job = _job(tmp_path)
    job.save_artifact("plan", "plan.json", json.dumps({"ok": 1}))
    assert job.load_artifact("plan") == {"ok": 1}


@pytest.mark.parametrize("escape", ["../outside.json", "ABSOLUTE"])
def test_load_artifact_refuses_paths_outside_the_job_folder(tmp_path, escape):
    from core.exceptions import ArtifactMissingError

    job = _job(tmp_path)
    job.save_artifact("plan", "plan.json", json.dumps({"ok": 1}))
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"secret": 1}), encoding="utf-8")
    job.state.artifacts["plan"].filename = (
        str(outside) if escape == "ABSOLUTE" else escape)
    with pytest.raises(ArtifactMissingError):
        job.load_artifact("plan")


def test_document_outside_the_job_folder_still_resumes(tmp_path):
    """المستند يُسجَّل بمسار مطلق خارج المجلد عمدًا — الحصر لا يكسر استئنافه."""
    job = _job(tmp_path)
    docx = tmp_path / "out" / "a.docx"
    docx.parent.mkdir()
    docx.write_bytes(b"docx")
    job.register_artifact("document", docx)
    assert job.has_artifact("document")


# ---------------------------------------------------------------------
# SEC-07 وREL-01
# ---------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent


def test_wrangler_cache_is_ignored():
    assert "server/.wrangler/" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_release_publishes_installer_checksums():
    build = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    assert "SHA256SUMS.txt" in build and "Get-FileHash" in build
