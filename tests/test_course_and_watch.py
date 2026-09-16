"""بوابات تقرير المقرّر ووضع المراقبة والترجمة.

ثلاثة انحدارات هنا تكلّف ليلةً كاملة إن وقعت، ولا يكشفها تشغيلٌ يدويّ
على جهاز واحد:

* ملفّ يُلتقط وهو ما زال يُنسخ عبر الشبكة، فيُفرّغ نصف محاضرة.
* ملفّ فاشل لا يُسجَّل، فيُعاد تفريغه كل دورة إلى الأبد ويحرق الطابور.
* ترجمة تُزيح البطاقات، فتصير كل جملة على توقيت جملة أخرى.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from ai.providers import LLMProvider, ProviderInfo
from ai.translator import TranslationConfig, translate_cues
from config.schemas import AudioSegment, TranscriptionResult
from core.course import build_report
from core.watcher import (
    WatchState,
    fingerprint,
    is_stable,
    pending_files,
    process_once,
)


# ---------------------------------------------------------------------
# تجهيز مجلد مهامّ وهميّ
# ---------------------------------------------------------------------
def _make_job(root: Path, name: str, *, status: str = "completed",
              duration: float = 600.0, outputs=(), sections: int = 3,
              questions: int = 0, quality: float = 78.0) -> Path:
    job = root / name
    job.mkdir(parents=True)
    (job / "job_state.json").write_text(json.dumps({
        "status": status, "stage": "document", "progress": 100.0,
        "video_path": str(root / f"{name}.mp4"),
        "updated_at": "2026-09-15T10:00:00",
        "last_error": "" if status != "failed" else "ffprobe سقط",
        "artifacts": {},
    }), encoding="utf-8")
    (job / "metadata.json").write_text(
        json.dumps({"duration_seconds": duration}), encoding="utf-8")
    (job / "plan.json").write_text(json.dumps({
        "title": name, "quality_score": quality, "quality_warnings": [],
        "sections": [{"title": f"ق{i}", "blocks": [
            {"kind": "paragraph"}, {"kind": "figure"}]}
            for i in range(sections)],
    }, ensure_ascii=False), encoding="utf-8")
    if questions:
        (job / "study.json").write_text(json.dumps({
            "questions": [{"question": "س", "answer": "ج"}] * questions,
            "glossary": [],
        }, ensure_ascii=False), encoding="utf-8")
    for suffix in outputs:
        (job / f"{name}{suffix}").write_text("x", encoding="utf-8")
    return job


# ---------------------------------------------------------------------
# تقرير المقرّر
# ---------------------------------------------------------------------
def test_report_orders_lectures_oldest_first(tmp_path):
    """ترتيب المقرّر زمنيّ — و``list_jobs`` تعيد الأحدث أولًا لغرض آخر."""
    for index, stamp in enumerate(
            ("2026-09-13T08:00:00", "2026-09-14T08:00:00",
             "2026-09-15T08:00:00"), start=1):
        job = _make_job(tmp_path, f"محاضرة{index}")
        state = json.loads((job / "job_state.json").read_text(encoding="utf-8"))
        state["updated_at"] = stamp
        (job / "job_state.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8")

    assert [row.name for row in build_report(tmp_path).lectures] == [
        "محاضرة1", "محاضرة2", "محاضرة3"]


def test_lectures_written_in_the_same_second_keep_a_stable_order(tmp_path):
    """دفعةٌ آليّة تكتب حالاتها في الثانية نفسها — الاسم هو الفاصل."""
    for name in ("ج", "أ", "ب"):
        _make_job(tmp_path, name)
    first = [row.name for row in build_report(tmp_path).lectures]
    assert first == sorted(first)
    assert first == [row.name for row in build_report(tmp_path).lectures]


def test_report_totals_are_summed_across_lectures(tmp_path):
    _make_job(tmp_path, "أ", duration=600.0, sections=3, questions=5)
    _make_job(tmp_path, "ب", duration=1200.0, sections=2, questions=7)
    report = build_report(tmp_path)

    assert report.total == 2 and report.completed == 2
    assert report.total_seconds == 1800.0
    assert report.total_questions == 12
    assert sum(row.figures for row in report.lectures) == 5


def test_missing_outputs_become_a_remedy_with_a_command(tmp_path):
    """تقريرٌ يقول «ينقص كذا» بلا أمر الإصلاح يترك القارئ حيث وجده."""
    _make_job(tmp_path, "أ", outputs=(".docx", ".html"))
    report = build_report(tmp_path)

    assert ".pdf" in report.lectures[0].missing
    assert report.remedies["--export pdf"] == 1
    # ``--study`` يُخرج الدليل والبطاقات معًا: محاضرةٌ واحدة لا اثنتان
    assert report.remedies["--study"] == 1


def test_an_incomplete_lecture_is_not_counted_as_missing_everything(tmp_path):
    """محاضرةٌ توقّفت ينقصها كل شيء — وإدراجه يُغرق التغطية بضجيج."""
    _make_job(tmp_path, "ناقصة", status="running", outputs=())
    report = build_report(tmp_path)

    assert report.lectures[0].missing == []
    assert any("تابع المهمّة" in remedy for remedy in report.remedies)


def test_a_failed_lecture_surfaces_its_error(tmp_path):
    _make_job(tmp_path, "ساقطة", status="failed")
    report = build_report(tmp_path)
    assert "ffprobe" in report.lectures[0].last_error
    assert any("راجع الخطأ" in remedy for remedy in report.remedies)


def test_study_guide_is_not_miscounted_as_the_interactive_page(tmp_path):
    """‏``.study.html`` و``.html`` كلاهما ينتهي بـ``.html``."""
    _make_job(tmp_path, "أ", outputs=(".docx", ".study.html"))
    row = build_report(tmp_path).lectures[0]
    assert ".study.html" in row.outputs
    assert ".html" not in row.outputs


def test_report_renders_without_external_references(tmp_path):
    from document.course_report import render_html

    _make_job(tmp_path, "أ", outputs=(".docx", ".html"))
    _make_job(tmp_path, "ب", status="failed")
    page = render_html(build_report(tmp_path, "شبكات ٢٠١"), tmp_path)

    assert "شبكات ٢٠١" in page
    assert "http://" not in page and "https://" not in page
    assert "تقرير التغطية" in page


def test_report_on_an_empty_folder_is_a_page_not_a_crash(tmp_path):
    from document.course_report import render_html

    report = build_report(tmp_path)
    assert report.total == 0
    assert "لا مهامّ" in render_html(report, tmp_path)


# ---------------------------------------------------------------------
# وضع المراقبة
# ---------------------------------------------------------------------
def test_a_file_still_being_copied_is_not_picked_up(tmp_path):
    """العطل الذي يقع حتمًا ولا يظهر في اختبار على جهاز واحد."""
    growing = tmp_path / "درس.mp4"
    growing.write_bytes(b"x" * 1000)

    import core.watcher as watcher

    real_sleep = time.sleep

    def grow(_seconds):
        # يحاكي النسخ عبر الشبكة: الملفّ يكبر بين الفحصين
        with open(growing, "ab") as handle:
            handle.write(b"y" * 1000)
        real_sleep(0.01)

    watcher.time.sleep = grow
    try:
        assert is_stable(growing, wait=0.01) is False
    finally:
        watcher.time.sleep = real_sleep

    assert is_stable(growing, wait=0.01) is True      # استقرّ الآن


def test_an_empty_file_is_never_considered_stable(tmp_path):
    empty = tmp_path / "فارغ.mp4"
    empty.touch()
    assert is_stable(empty, wait=0.01) is False


def test_only_unseen_media_files_are_pending(tmp_path):
    (tmp_path / "درس.mp4").write_bytes(b"x")
    (tmp_path / "ملاحظات.txt").write_text("x", encoding="utf-8")
    (tmp_path / ".مخفي.mp4").write_bytes(b"x")
    state = WatchState.load(tmp_path)

    assert [p.name for p in pending_files(tmp_path, state)] == ["درس.mp4"]

    state.mark(fingerprint(tmp_path / "درس.mp4"), ok=True)
    assert pending_files(tmp_path, state) == []


def test_an_edited_file_is_processed_again(tmp_path):
    """بصمةٌ من الاسم والحجم ووقت التعديل: النسخة المحرَّرة تُعاد."""
    source = tmp_path / "درس.mp4"
    source.write_bytes(b"x" * 10)
    state = WatchState.load(tmp_path)
    state.mark(fingerprint(source), ok=True)
    assert pending_files(tmp_path, state) == []

    source.write_bytes(b"x" * 999)
    assert [p.name for p in pending_files(tmp_path, state)] == ["درس.mp4"]


def test_a_failed_file_is_recorded_so_it_is_not_retried_forever(
        tmp_path, monkeypatch):
    """بلا تسجيل الفاشل يُعاد تفريغ ملفّ تالف كل دورة ويحرق الليلة."""
    import core.watcher as watcher
    from core.batch import BatchResult

    inbox, out = tmp_path / "in", tmp_path / "out"
    inbox.mkdir()
    (inbox / "تالف.mp4").write_bytes(b"x" * 500)

    monkeypatch.setattr(watcher, "is_stable", lambda *a, **k: True)
    monkeypatch.setattr(
        "core.batch.process_folder",
        lambda sources, *a, **k: [
            BatchResult(source=s, ok=False, error="ملفّ تالف") for s in sources])

    assert watcher.process_once(inbox, out) == []
    state = WatchState.load(inbox)
    assert len(state.entries) == 1
    assert pending_files(inbox, state) == []      # لا يُعاد


def test_the_ledger_lives_beside_the_files_it_describes(tmp_path):
    """نقل المجلد ينقل سجلّه، فلا يُعاد تفريغ مقرّر كامل."""
    from core.watcher import LEDGER_FILENAME

    state = WatchState.load(tmp_path)
    state.mark("k", ok=True)
    assert (tmp_path / LEDGER_FILENAME).is_file()


def test_a_corrupt_ledger_starts_over_instead_of_crashing(tmp_path):
    from core.watcher import LEDGER_FILENAME

    (tmp_path / LEDGER_FILENAME).write_text("{ليس JSON", encoding="utf-8")
    assert WatchState.load(tmp_path).entries == {}


def test_a_failing_cycle_does_not_kill_the_service(tmp_path, monkeypatch):
    """خدمةٌ تعمل ليلًا تموت على ملفّ واحد = طابورٌ واقف حتى الصباح."""
    import core.watcher as watcher

    def boom(*args, **kwargs):
        raise RuntimeError("عطل مفتعل")

    monkeypatch.setattr(watcher, "process_once", boom)
    monkeypatch.setattr(watcher, "_sleep_interruptible", lambda *a, **k: None)
    assert watcher.watch(tmp_path / "in", tmp_path / "out",
                         max_cycles=3) == 0      # لم يرمِ


def test_watch_stops_when_cancelled(tmp_path):
    from utils.cancellation import CancellationToken

    import core.watcher as watcher

    token = CancellationToken()
    token.cancel()
    assert watcher.watch(tmp_path / "in", tmp_path / "out",
                         cancel_token=token, max_cycles=50) == 0


# ---------------------------------------------------------------------
# الترجمة
# ---------------------------------------------------------------------
class FakeTranslator(LLMProvider):
    info = ProviderInfo(name="fake", label_ar="وهمي", is_local=True,
                        privacy_note="")

    def __init__(self, mode: str = "good") -> None:
        self.mode = mode
        self.calls = 0

    def is_available(self) -> bool:
        return True

    def complete(self, system_prompt, user_prompt, max_tokens=2048,
                 timeout=180) -> str:
        self.calls += 1
        lines = [line for line in user_prompt.splitlines() if "|" in line]
        if self.mode == "merged":
            lines = lines[:-1]            # يدمج بطاقتين — أشيع انحراف
        elif self.mode == "garbage":
            return "ليست بالشكل المطلوب إطلاقًا"
        return "\n".join(
            line.split("|", 1)[0] + "|EN:" + line.split("|", 1)[1]
            for line in lines)


def _cues():
    return [(0.0, 2.0, "الأولى"), (2.0, 4.0, "الثانية"), (4.0, 6.0, "الثالثة")]


def test_translation_never_touches_the_timings():
    """النموذج لا يُسأل عن التوقيت — من يُمليه يخترعه."""
    out = translate_cues(_cues(), FakeTranslator(), TranslationConfig())
    assert [(s, e) for s, e, _ in out] == [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)]
    assert [t for _s, _e, t in out] == ["EN:الأولى", "EN:الثانية", "EN:الثالثة"]


def test_a_batch_that_changes_the_card_count_is_rejected_whole():
    """دمج بطاقتين يُزيح الملفّ كلّه بعدهما — ترجمةٌ على توقيتٍ خطأ."""
    out = translate_cues(_cues(), FakeTranslator("merged"), TranslationConfig())
    assert [t for _s, _e, t in out] == ["الأولى", "الثانية", "الثالثة"]


def test_an_unparsable_reply_falls_back_to_the_original_text():
    """فجوةٌ في ملفّ الترجمة تبدو عطبًا؛ سطرٌ بالعربية يبدو غير مترجَم."""
    out = translate_cues(_cues(), FakeTranslator("garbage"), TranslationConfig())
    assert len(out) == 3
    assert all(text for _s, _e, text in out)


def test_translated_files_carry_the_language_code(tmp_path):
    """المشغّلات تقرأ الرمز من الاسم فتعرض «English» لا «Track 2»."""
    from ai.translator import translate_subtitles

    segments = [AudioSegment(id=1, start=0.0, end=3.0,
                             text_raw="مرحبًا", text_clean="مرحبًا")]
    transcript = TranscriptionResult(language="ar", full_text_raw="مرحبًا",
                                     full_text_clean="مرحبًا", segments=segments)
    written = translate_subtitles(
        transcript, tmp_path / "درس.docx", FakeTranslator(),
        TranslationConfig(target="en"))
    assert sorted(p.name for p in written) == ["درس.en.srt", "درس.en.vtt"]
    assert "EN:مرحبًا" in (tmp_path / "درس.en.srt").read_text(encoding="utf-8")


def test_translation_batches_respect_the_batch_size():
    provider = FakeTranslator()
    cues = [(float(i), float(i) + 1, f"س{i}") for i in range(10)]
    translate_cues(cues, provider, TranslationConfig(batch_size=4))
    assert provider.calls == 3          # 4 + 4 + 2
