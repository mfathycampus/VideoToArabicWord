"""بوابات الحزمة التعليمية — وأثقلها بوابة التحقّق.

ما تحرسه هذه الاختبارات هو ADR-018: **ما لا يُثبت مصدره يُسقَط.** وهو
انحدارٌ لا تكشفه عين: حزمةٌ نصفُها مخترَع تبدو حزمةً ممتازة حتى يقرأها
من ألقى المحاضرة. فالفحص هنا يحقن ردودًا مخترَعة عمدًا — سؤالًا يستشهد
بمقطع غير موجود، وإجابةً خارج خياراتها، ومصطلحًا لم يُذكر — ويتأكّد أن
كلًّا منها سقط، وأن السليم نجا.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from ai.providers import LLMProvider, ProviderInfo, RewriteUnavailableError
from ai.study_builder import StudyBuilder, StudyConfig, build_without_model
from ai.study_verify import fold, verify
from config.schemas import (
    AudioSegment,
    DocumentPlan,
    DocumentSection,
    Flashcard,
    GlossaryTerm,
    KeyframeMetadata,
    LearningObjective,
    QuizQuestion,
    StudyPack,
    TranscriptionResult,
)

SEGMENTS = [
    (1, 0.0, 12.0, "الشبكة مجموعة أجهزة متصلة تتبادل البيانات وفق بروتوكول."),
    (2, 12.0, 25.0, "بروتوكول TCP يضمن وصول البيانات وترتيبها الصحيح."),
    (3, 25.0, 40.0, "أما UDP فيتخلّى عن الضمان مقابل سرعة أعلى."),
]


def _transcript() -> TranscriptionResult:
    segments = [AudioSegment(id=i, start=a, end=b, text_raw=t, text_clean=t)
                for i, a, b, t in SEGMENTS]
    body = " ".join(s.text_clean for s in segments)
    return TranscriptionResult(language="ar", full_text_raw=body,
                               full_text_clean=body, segments=segments)


def _plan() -> DocumentPlan:
    return DocumentPlan(title="مقدّمة في الشبكات",
                        sections=[DocumentSection(title="القسم الأول")])


class FakeProvider(LLMProvider):
    """مزوّد يعيد ردودًا مُعدّة — بلا شبكة ولا نموذج ولا تكلفة."""

    info = ProviderInfo(name="fake", label_ar="وهمي", is_local=True,
                        privacy_note="")

    def __init__(self, replies, available: bool = True) -> None:
        self.replies = list(replies)
        self.calls = 0
        self._available = available

    def is_available(self) -> bool:
        return self._available

    def complete(self, system_prompt, user_prompt, max_tokens=2048,
                 timeout=180) -> str:
        self.calls += 1
        if not self.replies:
            return "{}"
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(
            reply, ensure_ascii=False)


# ---------------------------------------------------------------------
# التطبيع
# ---------------------------------------------------------------------
@pytest.mark.parametrize("left,right", [
    ("الإجابة", "الاجابة"),          # همزة
    ("القوّة.", "القوه"),             # تاء مربوطة وترقيم
    ("  TCP  ", "tcp"),
    ("مُعَرَّف", "معرف"),              # تشكيل
])
def test_fold_ignores_differences_a_reader_does_not_see(left, right):
    """إسقاط سؤالٍ صحيح بسبب همزة خطأٌ بقدر قبول سؤال مخترع."""
    assert fold(left) == fold(right)


# ---------------------------------------------------------------------
# التحقّق
# ---------------------------------------------------------------------
def test_question_citing_a_nonexistent_segment_is_dropped():
    pack = StudyPack(questions=[
        QuizQuestion(kind="short", question="ما البروتوكول الذي يضمن الترتيب؟",
                     answer="TCP", segment_ids=[2]),
        QuizQuestion(kind="short", question="سؤال مخترع عن مقطع لا وجود له",
                     answer="س", segment_ids=[99]),
    ])
    result = verify(pack, _transcript())
    assert len(result.questions) == 1
    assert result.questions[0].segment_ids == [2]
    assert any("بلا مقطع مصدر" in reason for reason in result.dropped)


def test_answer_outside_its_own_options_is_dropped():
    """سؤالٌ لا حلّ له: يظنّ الطالب الخطأ خطأه."""
    pack = StudyPack(questions=[QuizQuestion(
        kind="mcq", question="أي البروتوكولات يضمن الترتيب؟",
        options=["UDP", "ICMP", "ARP"], answer="TCP", segment_ids=[2])])
    result = verify(pack, _transcript())
    assert result.questions == []
    assert any("خارج الخيارات" in reason for reason in result.dropped)


def test_mcq_with_fewer_than_three_distinct_options_is_dropped():
    pack = StudyPack(questions=[QuizQuestion(
        kind="mcq", question="أي البروتوكولات يضمن الترتيب؟",
        options=["TCP", "TCP ", "tcp"], answer="TCP", segment_ids=[2])])
    assert verify(pack, _transcript()).questions == []


def test_answer_matching_an_option_only_after_folding_is_kept():
    pack = StudyPack(questions=[QuizQuestion(
        kind="mcq", question="ما البروتوكول الذي يضمن الترتيب؟",
        options=["الإجابة الأولى", "UDP", "ICMP"],
        answer="الاجابة الاولى", segment_ids=[2])])
    result = verify(pack, _transcript())
    assert len(result.questions) == 1
    # النصّ المكتوب للطالب هو صيغة **الخيار** لا صيغة الإجابة
    assert result.questions[0].answer == "الإجابة الأولى"


@pytest.mark.parametrize("raw,expected", [
    ("صحيح", "صح"), ("true", "صح"), ("نعم", "صح"),
    ("خطا", "خطأ"), ("false", "خطأ"), ("لا", "خطأ"),
])
def test_true_false_answers_are_normalized(raw, expected):
    pack = StudyPack(questions=[QuizQuestion(
        kind="true_false", question="‏TCP يضمن ترتيب البيانات.",
        answer=raw, segment_ids=[2])])
    assert verify(pack, _transcript()).questions[0].answer == expected


def test_true_false_with_an_unreadable_answer_is_dropped():
    pack = StudyPack(questions=[QuizQuestion(
        kind="true_false", question="‏TCP يضمن ترتيب البيانات.",
        answer="ربما أحيانًا", segment_ids=[2])])
    assert verify(pack, _transcript()).questions == []


def test_glossary_term_absent_from_the_source_is_dropped():
    """أشيع هلوسات المسرد: مصطلح صحيح في مادّته، غائب عن المحاضرة."""
    pack = StudyPack(glossary=[
        GlossaryTerm(term="TCP", definition="بروتوكول موثوق", segment_ids=[2]),
        GlossaryTerm(term="BGP", definition="بروتوكول توجيه", segment_ids=[2]),
    ])
    result = verify(pack, _transcript())
    assert [g.term for g in result.glossary] == ["TCP"]
    assert any("لا يرد في المصدر" in reason for reason in result.dropped)


def test_glossary_term_found_only_on_screen_is_kept():
    """المكتوب على الشاشة أوثق من المنطوق: لا يحمل خطأ تفريغ أصلًا."""
    pack = StudyPack(glossary=[GlossaryTerm(
        term="Drop Classes", origin="screen")])
    result = verify(pack, _transcript(), screen_texts=["Drop Classes"])
    assert [g.term for g in result.glossary] == ["Drop Classes"]


def test_verification_fills_timestamps_from_segments_not_from_the_model():
    """النموذج لا يُسأل عن التوقيت إطلاقًا — من يُمليه يخترعه."""
    pack = StudyPack(questions=[QuizQuestion(
        kind="short", question="ما الفرق بين البروتوكولين؟",
        answer="الضمان", segment_ids=[2, 3])])
    question = verify(pack, _transcript()).questions[0]
    assert (question.source_start, question.source_end) == (12.0, 40.0)


def test_duplicates_are_collapsed_across_every_kind():
    pack = StudyPack(
        objectives=[LearningObjective(text="يميّز بين TCP و UDP", segment_ids=[2]),
                    LearningObjective(text="يميز بين TCP و UDP", segment_ids=[3])],
        flashcards=[Flashcard(front="TCP", back="موثوق", segment_ids=[2]),
                    Flashcard(front="tcp", back="مكرّر", segment_ids=[2])])
    result = verify(pack, _transcript())
    assert len(result.objectives) == 1
    assert len(result.flashcards) == 1


# ---------------------------------------------------------------------
# المولّد
# ---------------------------------------------------------------------
def _good_reply() -> dict:
    return {
        "objectives": [{"text": "يميّز بين TCP و UDP", "s": [2, 3]}],
        "glossary": [{"term": "TCP", "definition": "بروتوكول موثوق", "s": [2]}],
        "questions": [
            {"kind": "mcq", "q": "أي البروتوكولات يضمن الترتيب؟",
             "options": ["TCP", "UDP", "ICMP", "ARP"], "answer": "TCP",
             "why": "لأنه يضمن الوصول والترتيب", "level": "easy", "s": [2]},
            {"kind": "true_false", "q": "‏UDP أسرع من TCP.", "answer": "صح",
             "s": [3]},
        ],
        "flashcards": [{"front": "UDP", "back": "سرعة بلا ضمان", "s": [3]}],
    }


def test_builder_produces_a_traceable_pack():
    provider = FakeProvider([_good_reply()])
    pack = StudyBuilder(provider, StudyConfig(batch_chars=10_000)).build(
        _plan(), _transcript())

    assert pack.generated_by.startswith("ai:")
    assert len(pack.questions) == 2
    # الضمانة المركزية: لا عنصر بلا مصدر متحقَّق منه
    assert all(q.segment_ids for q in pack.questions)
    assert all(o.segment_ids for o in pack.objectives)


def test_one_failed_batch_does_not_lose_the_others():
    """عقد الفشل منقول من ai/rewriter: الدفعة الساقطة لا تُسقط البقية."""
    provider = FakeProvider([RuntimeError("عطل في دفعة واحدة"), _good_reply()])
    pack = StudyBuilder(provider, StudyConfig(batch_chars=60)).build(
        _plan(), _transcript())
    assert pack.questions  # الدفعة الناجحة وصلت رغم سقوط الأولى


def test_a_fatal_provider_error_stops_the_remaining_batches():
    """مفتاحٌ خاطئ لن يصلح في الدفعة العشرين.

    الاستمرار عليه يعني تكرار الفشل نفسه أمام المستخدم مرّةً لكل دفعة —
    وعلى مزوّد سحابي: عشرين نداءً مرفوضًا بدل واحد.
    """
    provider = FakeProvider(
        [RewriteUnavailableError("مفتاح خاطئ", retryable=False)] * 5)
    StudyBuilder(provider, StudyConfig(batch_chars=40)).build(
        _plan(), _transcript())
    assert provider.calls == 1


def test_an_invalid_json_reply_is_recorded_not_raised():
    provider = FakeProvider(["ليس JSON إطلاقًا"])
    pack = StudyBuilder(provider, StudyConfig(batch_chars=10_000)).build(
        _plan(), _transcript())
    assert pack.is_empty() or not pack.questions
    assert any("ردّ غير صالح" in reason for reason in pack.dropped)


def test_builder_stops_calling_once_the_question_cap_is_reached():
    """السقف يوقف النداءات لا يقتطع نتائجها — النداء بعده مالٌ يُهدر."""
    provider = FakeProvider([_good_reply()] * 8)
    config = StudyConfig(batch_chars=40, max_questions=2)
    StudyBuilder(provider, config).build(_plan(), _transcript())
    assert provider.calls <= 2


def test_model_invented_content_is_stripped_end_to_end():
    reply = _good_reply()
    reply["questions"].append(
        {"kind": "mcq", "q": "سؤال عن بروتوكول لم يُذكر إطلاقًا",
         "options": ["BGP", "OSPF", "RIP"], "answer": "BGP", "s": [404]})
    reply["glossary"].append({"term": "Kerberos", "definition": "مخترع", "s": [2]})

    pack = StudyBuilder(FakeProvider([reply]),
                        StudyConfig(batch_chars=10_000)).build(
        _plan(), _transcript())
    assert not any("لم يُذكر" in q.question for q in pack.questions)
    assert not any(g.term == "Kerberos" for g in pack.glossary)
    assert len(pack.dropped) >= 2


# ---------------------------------------------------------------------
# المسار بلا نموذج
# ---------------------------------------------------------------------
def test_without_a_model_a_term_list_is_still_produced():
    segments = [AudioSegment(id=i, start=float(i), end=float(i) + 1,
                             text_raw="نستعمل Drop Classes هنا",
                             text_clean="نستعمل Drop Classes هنا")
                for i in range(1, 6)]
    body = " ".join(s.text_clean for s in segments)
    transcript = TranscriptionResult(language="ar", full_text_raw=body,
                                     full_text_clean=body, segments=segments)
    keyframes = [KeyframeMetadata(
        image_id=1, timestamp=1.0, filename="f1.jpg", scene_id=1,
        change_score=0.5, width=10, height=10, selection_reason="t",
        ocr_text="Drop Classes")]

    pack = build_without_model(_plan(), transcript, keyframes)
    assert pack.generated_by == "screen"
    assert pack.glossary
    # الصدق المُعلَن: قائمة مصطلحات بلا ادّعاء تعريفات
    assert all(term.definition == "" for term in pack.glossary)


# ---------------------------------------------------------------------
# التصيير
# ---------------------------------------------------------------------
def _built_pack() -> StudyPack:
    return verify(StudyPack(title="الشبكات", **{
        "questions": [QuizQuestion(
            kind="mcq", question="أي البروتوكولات يضمن الترتيب؟",
            options=["TCP", "UDP", "ICMP"], answer="TCP",
            explanation="يضمن الوصول والترتيب", segment_ids=[2])],
        "objectives": [LearningObjective(text="يميّز بين TCP و UDP",
                                         segment_ids=[2])],
        "glossary": [GlossaryTerm(term="TCP", definition="موثوق",
                                  segment_ids=[2])],
        "flashcards": [Flashcard(front="UDP", back="سريع", segment_ids=[3])],
    }), _transcript())


def test_study_html_hides_answers_and_keeps_the_source_stamp():
    from document.exporters.study_export import render_study_html

    text = render_study_html(_built_pack())
    assert 'class="answer hidden"' in text        # مخفيّة خلف زرّ
    assert 'class="ts" data-t="12.000"' in text   # مرجعٌ يقفز للمقطع
    assert "http://" not in text and "https://" not in text


def test_study_html_escapes_model_output():
    from document.exporters.study_export import render_study_html

    pack = _built_pack()
    pack.questions[0].question = '<img src=x onerror="alert(1)">'
    text = render_study_html(pack)
    # الوسم لا يصل إلى المتصفّح وسمًا: لا قوس زاوية غير مهروب، ولا
    # علامة اقتباس تُغلق خاصيّة. الاكتفاء بغياب ``onerror`` كنصّ خطأ —
    # النصّ المهروب يحويه سليمًا وعاجزًا.
    assert "<img" not in text
    assert 'onerror="' not in text
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in text


def test_flashcards_csv_opens_correctly_in_excel(tmp_path):
    """‏BOM ليس تفصيلًا: بدونه يفتح Excel العربية محارفَ مشوّهة."""
    from config.settings import DocumentConfig
    from document.exporters import ExportContext
    from document.exporters.study_export import export_flashcards

    ctx = ExportContext(
        plan=_plan(),
        metadata=__import__("tests.test_exporters", fromlist=["x"])._metadata(tmp_path),
        images_dir=tmp_path, base_path=tmp_path / "درس.docx",
        document_config=DocumentConfig(),
        options={"study_pack": _built_pack()})

    path = export_flashcards(ctx)
    assert path.name == "درس.flashcards.csv"
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert "UDP" in path.read_text(encoding="utf-8-sig")


def test_renderers_skip_themselves_silently_when_the_feature_is_off(tmp_path):
    from config.settings import DocumentConfig
    from document.exporters import ExportContext
    from document.exporters.study_export import export_flashcards, export_study

    ctx = ExportContext(
        plan=_plan(),
        metadata=__import__("tests.test_exporters", fromlist=["x"])._metadata(tmp_path),
        images_dir=tmp_path, base_path=tmp_path / "درس.docx",
        document_config=DocumentConfig())
    assert export_study(ctx) is None
    assert export_flashcards(ctx) is None


def test_rebuild_reads_the_saved_pack_without_calling_any_model(tmp_path):
    """‏--rebuild يجب ألا يكلّف نداءً واحدًا: study.json هو المصدر."""
    from config.settings import DocumentConfig
    from document.exporters import ExportContext
    from document.exporters.study_export import export_study

    (tmp_path / "study.json").write_text(
        _built_pack().model_dump_json(), encoding="utf-8")
    ctx = ExportContext(
        plan=_plan(),
        metadata=__import__("tests.test_exporters", fromlist=["x"])._metadata(tmp_path),
        images_dir=tmp_path, base_path=tmp_path / "درس.docx",
        document_config=DocumentConfig())

    path = export_study(ctx)
    assert path is not None and "أي البروتوكولات" in path.read_text(encoding="utf-8")
