"""بناء v1.22.0 سقط بـHTTP 504 واحد أثناء تنزيل حزمة لغة Tesseract."""
import importlib.util
import io
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tool():
    spec = importlib.util.spec_from_file_location("setup_tess", ROOT / "tools" / "setup_tesseract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _http(code):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b""))


def test_transient_errors_are_retried_across_mirrors(tool, monkeypatch, tmp_path):
    tried = []

    def once(url, destination):
        tried.append(url)
        if len(tried) < 3:
            raise _http(504)
        destination.write_bytes(b"ok")

    monkeypatch.setattr(tool, "_download_once", once)
    waits = []
    tool._download(["a", "b"], tmp_path / "ara.traineddata", sleep=waits.append)
    assert tried == ["a", "b", "a"] and waits == [5, 10]
    assert (tmp_path / "ara.traineddata").read_bytes() == b"ok"


def test_not_found_is_not_retried(tool, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tool, "_download_once",
                        lambda url, d: (calls.append(url), (_ for _ in ()).throw(_http(404))))
    with pytest.raises(urllib.error.HTTPError):
        tool._download(["a", "b"], tmp_path / "x", sleep=lambda s: None)
    assert calls == ["a"]


def test_gives_up_after_all_attempts(tool, monkeypatch, tmp_path):
    def once(url, destination):
        raise _http(503)

    monkeypatch.setattr(tool, "_download_once", once)
    with pytest.raises(SystemExit):
        tool._download(["a"], tmp_path / "x", sleep=lambda s: None)
