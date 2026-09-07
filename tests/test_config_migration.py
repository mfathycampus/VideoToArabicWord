"""ترقية الإعداد: افتراضٌ قديم محفوظ لا يجوز أن يفوز على تحسينٍ جديد.

سبب وجود هذا الملف عطلٌ صامت رأيتُه في سجلّ المستخدم:

    تحميل Whisper (large-v3-turbo) على cpu/int8 · خيوط=4 · beam=5

على معالج بأربع أنوية وثمانية خيوط. والافتراضي في الشيفرة صار ``0``
(اشتقاق من عدد الأنوية) منذ دفعة 582b812، ومعه تعليقٌ يقول بالحرف إن
«الرقم الثابت 4 كان يترك نصف الأداء على جهاز بثمانية أنوية». لكنّ ملف
الإعداد المحفوظ يحمل ``4`` من قبل ذلك التغيير، والمحفوظ يفوز.

أي أن التحسين وصل إلى المستخدم **الجديد** وحده، ولم يصل إلى من يستعمل
البرنامج منذ شهر. وهذا معكوس الترتيب الصحيح، وهو صنفُ عطل لا يظهر في
أي اختبار يبني الإعداد من الافتراضيات — وكلّ اختباراتنا كانت كذلك.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from config.settings import (SUPERSEDED_DEFAULTS, AppConfig,
                             drop_superseded_defaults)


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


# ── العطل نفسه ────────────────────────────────────────────────────────

def test_a_stale_thread_count_returns_to_automatic(tmp_path):
    """الحالة الحرفية من جهاز المستخدم."""
    path = _write(tmp_path, {"whisper": {"cpu_threads": 4}})
    assert AppConfig.load(path).whisper.cpu_threads == 0


def test_the_rest_of_the_saved_file_survives(tmp_path):
    """الترقية تُسقط حقلًا واحدًا، لا تُعيد الملفّ كلّه إلى الافتراضي."""
    path = _write(tmp_path, {
        "whisper": {"cpu_threads": 4, "beam_size": 1, "glossary": "سبورة"},
    })
    config = AppConfig.load(path)
    assert config.whisper.beam_size == 1
    assert config.whisper.glossary == "سبورة"


def test_what_the_user_chose_is_never_touched(tmp_path):
    """‏6 ليست افتراضًا سابقًا لأحد — فهي اختيار، ولا تُمسّ.

    هذا هو الحدّ الأخلاقي للترقية كلّها: نُسقط ما يساوي افتراضًا سابقًا
    بعينه ولا شيء غيره. بلا هذا الحدّ تصير الترقية إعادةَ ضبطٍ صامتة
    لخيارات المستخدم.
    """
    path = _write(tmp_path, {"whisper": {"cpu_threads": 6}})
    assert AppConfig.load(path).whisper.cpu_threads == 6


def test_an_already_automatic_file_is_left_alone(tmp_path):
    path = _write(tmp_path, {"whisper": {"cpu_threads": 0}})
    assert AppConfig.load(path).whisper.cpu_threads == 0


def test_the_change_is_written_to_the_log(tmp_path, caplog):
    """تغييرٌ صامت في إعداد المستخدم لا يجوز أن يمرّ بلا أثر."""
    import logging

    path = _write(tmp_path, {"whisper": {"cpu_threads": 4}})
    with caplog.at_level(logging.INFO):
        AppConfig.load(path)
    assert any("cpu_threads" in record.getMessage()
               for record in caplog.records)


# ── سلامة الجدول نفسه ─────────────────────────────────────────────────

def test_no_superseded_value_equals_the_current_default():
    """حارسٌ على الجدول: قيمةٌ تساوي الافتراضي الحالي تعني إسقاطًا بلا
    أثر — أو أسوأ، إسقاطَ اختيارٍ صار هو الافتراضي."""
    defaults = AppConfig()
    for section, fields in SUPERSEDED_DEFAULTS.items():
        current = getattr(defaults, section)
        for field, old_values in fields.items():
            assert hasattr(current, field), f"حقل غير موجود: {section}.{field}"
            assert getattr(current, field) not in old_values, (
                f"{section}.{field}: القيمة القديمة هي الافتراضي الحالي")


def test_the_function_reports_what_it_dropped():
    data = {"whisper": {"cpu_threads": 4}}
    assert drop_superseded_defaults(data) == ["whisper.cpu_threads"]
    assert "cpu_threads" not in data["whisper"]


def test_a_missing_section_is_not_an_error():
    assert drop_superseded_defaults({}) == []
    assert drop_superseded_defaults({"whisper": None}) == []


# ── الأثر الفعلي: عدد الخيوط المشتقّ ──────────────────────────────────

def test_automatic_threads_follow_the_machine(monkeypatch):
    """الغاية من الترقية كلّها — أن يصل هذا الاشتقاق إلى المستخدم.

    ثمانية خيوط منطقية (وهو ما يقوله ويندوز على معالج المستخدم:
    ‏Ryzen 5 PRO 3400GE بأربع أنوية وثمانية خيوط) ← سبعة، تُترك واحدة
    للنظام والواجهة.
    """
    from audio.transcriber import TranscriptionEngine
    from config.settings import TranscriptionConfig

    monkeypatch.setattr("os.cpu_count", lambda: 8)
    engine = TranscriptionEngine(TranscriptionConfig(cpu_threads=0))
    assert engine._resolve_threads() == 7


def test_an_explicit_thread_count_still_wins(monkeypatch):
    from audio.transcriber import TranscriptionEngine
    from config.settings import TranscriptionConfig

    monkeypatch.setattr("os.cpu_count", lambda: 8)
    engine = TranscriptionEngine(TranscriptionConfig(cpu_threads=2))
    assert engine._resolve_threads() == 2
