"""1.19.1 — من مراجعة 1.19.0: بصمة الحزمة التعليمية، وتعريفات المسرد،
ومطابقة الإجابات، وأسماء الأزرار في خطوات الشاشة."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from config.schemas import StudyPack


# ── بصمة الحزمة التعليمية ────────────────────────────────────────────
def _host(provider="anthropic"):
    from config.settings import AppConfig
    from core.pipeline_outputs import OutputsMixin

    config = AppConfig()
    config.study.enabled = True
    config.study.provider = provider
    host = OutputsMixin.__new__(OutputsMixin)
    host.config = config
    return host


def _job(transcript="aaa", plan="bbb"):
    def ref(checksum):
        return SimpleNamespace(checksum=checksum)

    return SimpleNamespace(state=SimpleNamespace(
        artifacts={"transcription": ref(transcript), "plan": ref(plan)}))


def test_fingerprint_changes_with_transcript_plan_and_settings():
    host = _host()
    base = host._study_fingerprint(_job())
    assert base == host._study_fingerprint(_job())
    assert base != host._study_fingerprint(_job(transcript="zzz"))
    assert base != host._study_fingerprint(_job(plan="zzz"))
    host.config.study.max_questions = 5
    assert base != host._study_fingerprint(_job())


def test_api_key_does_not_change_the_fingerprint():
    host = _host()
    base = host._study_fingerprint(_job())
    host.config.study.api_key = "sk-new"
    assert host._study_fingerprint(_job()) == base


def _pack(**kwargs):
    from config.schemas import QuizQuestion
    return StudyPack(generated_by="ai:anthropic", questions=[QuizQuestion(
        kind="short", question="ما الفرق بين المهمة والاختبار؟", answer="التصحيح",
        segment_ids=[1])], **kwargs)


@pytest.mark.parametrize("pack,expected", [
    (_pack(fingerprint="f1"), None),
    (_pack(), "بلا بصمة"),
    (_pack(fingerprint="old"), "تغيّر"),
    (_pack(fingerprint="f1", failures=["الدفعة 1/11: قُطع"]), "ناقصة"),
])
def test_only_a_complete_pack_built_from_the_same_inputs_is_reused(pack, expected):
    from core.pipeline_outputs import _study_reuse_blocker

    reason = _study_reuse_blocker(pack, "f1")
    assert (reason is None) if expected is None else (expected in reason)


# ── حالة المحاضرة في تقرير المقرّر ───────────────────────────────────
def _row(status, label, progress, stage=""):
    from core.course import LectureRow
    return LectureRow(name="م", job_dir=Path("."), status=status, status_label=label,
                      progress=progress, stage=stage)


def test_a_failed_lecture_names_its_stage_not_a_percentage():
    """مراجعة 1.19.0: «فشلت 100%» يُقرأ نجاحًا."""
    from document.course_report import _status_pill

    pill = _status_pill(_row("failed", "فشلت", 100.0, "transcription"))
    assert "فشلت عند «التفريغ»" in pill and "%" not in pill


def test_a_running_lecture_never_shows_100_percent():
    from document.course_report import _status_pill

    assert "99%" in _status_pill(_row("running", "قيد التشغيل", 100.0, "document"))
    assert "%" not in _status_pill(_row("completed", "مكتملة", 100.0))
