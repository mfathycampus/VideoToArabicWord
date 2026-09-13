"""فحص التحديث: أن يعمل، وألّا يعمل من تلقاء نفسه.

الشقّ الثاني هو الأهمّ. الواجهة تَعِد المستخدم بأن البرنامج «يعمل على
جهازك بالكامل — بلا إنترنت»، و‏ADR-014 يشترط إذنًا صريحًا لكل اتصال.
ففحصٌ يجري عند الإقلاع يُخلف الوعد المكتوب على الشاشة نفسها. وهذا صنف
عطلٍ لا يمسك نفسه: الميزة تعمل، والاختبارات تخضرّ، والوعد يُنقض بصمت.
"""
from __future__ import annotations

import io
import json
import re
import urllib.error
from pathlib import Path

import pytest

from utils.updater import (RELEASES_PAGE, UpdateCheck, check_for_update,
                           is_newer, parse_version)

ROOT = Path(__file__).resolve().parents[1]


def _response(payload: dict):
    """مُقلِّدٌ لِما يعيده ``urlopen`` — كائن سياق يُقرأ منه بايتات."""

    class _Fake(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()
            return False

    def opener(request, timeout=None):
        return _Fake(json.dumps(payload).encode("utf-8"))

    return opener


# ── المقارنة ──────────────────────────────────────────────────────────

def test_a_tag_with_or_without_v_is_the_same_number():
    assert parse_version("v1.5.0") == parse_version("1.5.0") == (1, 5, 0)


def test_ten_is_newer_than_nine():
    """المقارنة النصّية كانت ستقول العكس — بعد عشرة إصدارات ثانوية."""
    assert is_newer("1.10.0", "1.9.0")
    assert not is_newer("1.9.0", "1.10.0")


def test_the_same_version_is_not_an_update():
    assert not is_newer("1.5.0", "1.5.0")


def test_a_shorter_number_is_padded_not_truncated():
    assert not is_newer("1.5", "1.5.0")
    assert is_newer("1.5.1", "1.5")


# ── الفحص ─────────────────────────────────────────────────────────────

def test_a_newer_release_is_reported_with_its_page():
    result = check_for_update(
        current="1.5.0",
        opener=_response({"tag_name": "v1.6.0",
                          "html_url": "https://example.invalid/r/1.6.0"}))
    assert result.available
    assert result.latest == "1.6.0"
    assert result.page_url == "https://example.invalid/r/1.6.0"


def test_the_current_version_reports_no_update():
    result = check_for_update(
        current="1.5.0", opener=_response({"tag_name": "v1.5.0"}))
    assert not result.available
    assert "أحدث إصدار" in result.message


def test_no_internet_is_a_calm_sentence_not_a_crash():
    """الحال الطبيعي المقصود لهذا البرنامج: جهازٌ بلا إنترنت."""

    def offline(request, timeout=None):
        raise OSError("لا اتصال")

    result = check_for_update(current="1.5.0", opener=offline)
    assert not result.available
    assert result.error
    # ولا يُفهَم منها أن البرنامج تعطّل
    assert "لا يؤثّر على عمل البرنامج" in result.message


def test_a_repository_without_releases_is_not_an_error():
    """‏404 هنا يعني «لم يُنشر إصدارٌ بعد» لا «حدث عطل»."""

    def not_found(request, timeout=None):
        raise urllib.error.HTTPError(
            RELEASES_PAGE, 404, "Not Found", {}, None)

    result = check_for_update(current="1.5.0", opener=not_found)
    assert not result.error
    assert not result.available


def test_a_rate_limited_network_says_so_instead_of_crying_failure():
    """مدرسةٌ كاملة خلف عنوان IP واحد تتقاسم ستّين طلبًا في الساعة.

    ورسالة «تعذّر سؤال GitHub» هنا تدفع المعلّم إلى ظنّ أن البرنامج
    معطوب، بينما الصواب أن ينتظر أو يفتح الصفحة بنفسه.
    """

    def limited(request, timeout=None):
        raise urllib.error.HTTPError(
            RELEASES_PAGE, 403, "rate limit exceeded",
            {"x-ratelimit-remaining": "0"}, None)

    result = check_for_update(current="1.5.0", opener=limited)
    assert "يحدّ من عدد الطلبات" in result.error
    assert "المتصفّح" in result.error


def test_a_plain_403_is_not_mistaken_for_a_rate_limit():
    def forbidden(request, timeout=None):
        raise urllib.error.HTTPError(
            RELEASES_PAGE, 403, "Forbidden",
            {"x-ratelimit-remaining": "57"}, None)

    result = check_for_update(current="1.5.0", opener=forbidden)
    assert "يحدّ من عدد الطلبات" not in result.error
    assert "403" in result.error


def test_corrupt_json_does_not_escape_as_an_exception():
    class _Fake(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    result = check_for_update(
        current="1.5.0",
        opener=lambda request, timeout=None: _Fake(b"<html>not json</html>"))
    assert result.error


def test_the_request_carries_nothing_about_the_user():
    """طلبٌ مجهول: لا معرّف جهاز ولا اسم مستخدم ولا مسار."""
    seen = {}

    def capture(request, timeout=None):
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        raise OSError("يكفي — التقطنا الطلب")

    check_for_update(current="1.5.0", opener=capture)
    assert "?" not in seen["url"], "لا معاملات في العنوان"
    blob = json.dumps(seen, ensure_ascii=False)
    assert str(Path.home()) not in blob


# ── الحدّ: لا فحص بلا طلب ─────────────────────────────────────────────

def test_the_module_does_nothing_when_imported(monkeypatch):
    import importlib

    import urllib.request

    def forbidden(*args, **kwargs):
        raise AssertionError("الاستيراد وحده فتح اتصالًا")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    importlib.reload(importlib.import_module("utils.updater"))


def test_nothing_schedules_the_check_by_itself():
    """لا مؤقّت ولا خيط يستدعي الفحص — الاستدعاء بيد المستخدم."""
    source = (ROOT / "utils" / "updater.py").read_text(encoding="utf-8")
    for scheduler in ("QTimer", "threading.Timer", "singleShot"):
        assert scheduler not in source, f"{scheduler} يجدول فحصًا تلقائيًّا"


def test_the_check_starts_from_a_click_and_nowhere_else():
    """الحارس الحقيقي: ما يبدأ الفحص في الواجهة مربوطٌ بضغطة زرّ.

    ميزةٌ تعمل واختباراتٌ تخضرّ لا تمنع أن يُنقَل الاستدعاء يومًا إلى
    ``__init__`` أو إلى مؤقّت، فيصير الفحص تلقائيًّا — وهو ما يُخلف وعد
    الواجهة بلا أن يسقط أي اختبار آخر.
    """
    window = (ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    assert "UpdateCheckWorker(" in window, "الفحص غير مربوط بالواجهة أصلًا"

    before = window[:window.index("UpdateCheckWorker(")]
    method = re.findall(r"^    def (\w+)\(", before, re.M)[-1]

    assert re.search(rf"clicked\.connect\(self\.{method}\b", window), (
        f"‏{method} تبدأ الفحص ولا زرّ يستدعيها — "
        "أي أن الفحص صار يجري بلا طلب المستخدم")


def test_the_fetch_itself_lives_in_one_place_only():
    """الاستدعاء الشبكي في ``UpdateCheckWorker.run`` وحده.

    كل موضعٍ إضافيّ يستدعي ``check_for_update`` هو موضعٌ يمكن أن يصير
    تلقائيًّا بلا أن يمرّ بالحارس أعلاه.
    """
    callers = set()
    for path in ROOT.rglob("*.py"):
        parts = path.relative_to(ROOT).parts
        if parts[0] in {"tests", ".venv", "build", "dist"}:
            continue
        if "__pycache__" in parts:
            continue
        if "check_for_update(" in path.read_text(encoding="utf-8"):
            callers.add("/".join(parts))

    assert callers == {"utils/updater.py", "ui/worker.py"}, callers
