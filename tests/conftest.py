import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
import pytest

CORPUS = Path(__file__).resolve().parents[1] / "testdata" / "corpus"


@pytest.fixture(scope="session")
def corpus_manifest():
    return json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def corpus_dir():
    return CORPUS


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: اختبارات بطيئة")
    config.addinivalue_line("markers", "asr: تحتاج نموذج تفريغ")
