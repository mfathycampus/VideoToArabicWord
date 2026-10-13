"""اختبار المفتاح لا يفشل على ردٍّ قُطع عند السقف (ردّ وصل = اتصال سليم)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

from ai.providers import ResponseTruncatedError  # noqa: E402
from ui.worker import ProviderTestWorker  # noqa: E402


class _Provider:
    def __init__(self, outcome):
        self.outcome, self.max_tokens = outcome, None

    def complete(self, system, user, max_tokens=2048, timeout=180):
        self.max_tokens = max_tokens
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _run(provider):
    worker = ProviderTestWorker(provider)
    got = {}
    worker.succeeded.connect(lambda t: got.setdefault("ok", t))
    worker.failed.connect(lambda t: got.setdefault("err", t))
    worker.run()
    return got


def test_truncated_reply_counts_as_success():
    got = _run(_Provider(ResponseTruncatedError("قُطع", partial="جاهز، وأنا")))
    assert got == {"ok": "جاهز، وأنا"}


def test_normal_reply_succeeds_with_headroom():
    provider = _Provider("جاهز")
    assert _run(provider) == {"ok": "جاهز"}
    assert provider.max_tokens >= 64


def test_real_failures_still_fail():
    got = _run(_Provider(RuntimeError("401 invalid x-api-key")))
    assert got == {"err": "401 invalid x-api-key"}
