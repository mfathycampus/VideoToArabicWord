"""إعادة بناء الخطة عند تغيّر إعداد الصياغة.

انحدار على عطل حقيقي: شُغِّلت المهمة أولًا بلا صياغة فاكتملت وحُفظت
خطتها. ثم فُعِّلت الصياغة وأُعيد التشغيل — فتخطّى الاستئناف مرحلة بناء
الخطة (كانت «مكتملة») ولم يُستدعَ النموذج إطلاقًا. خرج المستند خامًا
**بلا أي رسالة خطأ**، وهو أسوأ أنواع الأعطال.
"""
import json
import shutil
from pathlib import Path

import pytest

from config.schemas import AudioSegment, DocumentPlan, TranscriptionResult
from config.settings import AppConfig
from core.job_state import Stage
from core.pipeline import VideoToDocPipeline
from tests.test_corpus_matrix import StubModelManager, StubTranscriber

CORPUS = Path(__file__).resolve().parents[1] / "testdata" / "corpus"
VIDEO = CORPUS / "h264_mp4.mp4"


class FakeRewritePipeline(VideoToDocPipeline):
    """يستبدل استدعاء النموذج بخطة ثابتة تُحاكي نجاح الصياغة."""

    def _build_plan(self, transcript, keyframes, video_path, metadata, emit):
        if self.config.rewrite.enabled:
            from config.schemas import DocumentBlock, DocumentSection
            sections = [DocumentSection(
                title="قسم مُعاد صياغته", level=1, summary="ملخّص",
                start_timestamp=0.0,
                blocks=[DocumentBlock(kind="paragraph", text="نص محرّر.",
                                      segment_ids=[s.id for s in transcript.segments])]
                       + [DocumentBlock(kind="figure", image_id=k.image_id,
                                        image_filename=k.filename,
                                        timestamp=k.timestamp)
                          for k in keyframes])]
            plan = DocumentPlan(
                title="عنوان من الذكاء الاصطناعي", abstract="ملخّص تنفيذي.",
                key_points=["نقطة"], sections=sections,
                generated_by=f"ai:{self.config.rewrite.provider}/test",
                total_segments_in=len(transcript.segments),
                total_segments_out=len(transcript.segments),
                total_figures_in=len(keyframes),
                total_figures_out=len(keyframes))
            return plan
        return super()._build_plan(transcript, keyframes, video_path,
                                   metadata, emit)


def make(out: Path, rewrite: bool) -> FakeRewritePipeline:
    config = AppConfig()
    config.rewrite.enabled = rewrite
    config.rewrite.provider = "anthropic"
    return FakeRewritePipeline(out, config, transcriber=StubTranscriber(),
                               model_manager=StubModelManager())


def read_plan(job_dir: Path) -> dict:
    return json.loads((job_dir / "plan.json").read_text(encoding="utf-8"))


def test_enabling_rewrite_rebuilds_an_existing_plan(tmp_path):
    """العطل الأصلي: خطة قديمة بلا صياغة كانت تُعاد استخدامها."""
    out = tmp_path / "out"
    first = make(out, rewrite=False)
    first.run(VIDEO)
    job_dir = first.job_dir_for(VIDEO)
    assert read_plan(job_dir)["generated_by"] == "timeline"

    make(out, rewrite=True).run(VIDEO)
    plan = read_plan(job_dir)
    assert plan["generated_by"].startswith("ai:anthropic"), \
        "لم تُعَد الخطة رغم تفعيل الصياغة"
    assert plan["title"] == "عنوان من الذكاء الاصطناعي"
    assert plan["abstract"]


def test_disabling_rewrite_rebuilds_back_to_timeline(tmp_path):
    out = tmp_path / "out"
    make(out, rewrite=True).run(VIDEO)
    job_dir = make(out, rewrite=True).job_dir_for(VIDEO)
    assert read_plan(job_dir)["generated_by"].startswith("ai:")

    make(out, rewrite=False).run(VIDEO)
    assert read_plan(job_dir)["generated_by"] == "timeline"


def test_unchanged_settings_reuse_the_plan(tmp_path):
    """بلا تغيير: تُعاد الخطة كما هي ولا يُستدعى النموذج ثانيةً."""
    out = tmp_path / "out"
    make(out, rewrite=True).run(VIDEO)
    job_dir = make(out, rewrite=True).job_dir_for(VIDEO)
    before = (job_dir / "plan.json").stat().st_mtime_ns

    make(out, rewrite=True).run(VIDEO)
    assert (job_dir / "plan.json").stat().st_mtime_ns == before, \
        "أُعيد بناء الخطة بلا داعٍ"


def test_switching_provider_rebuilds(tmp_path):
    out = tmp_path / "out"
    make(out, rewrite=True).run(VIDEO)
    job_dir = make(out, rewrite=True).job_dir_for(VIDEO)

    config = AppConfig()
    config.rewrite.enabled = True
    config.rewrite.provider = "ollama"
    FakeRewritePipeline(out, config, transcriber=StubTranscriber(),
                        model_manager=StubModelManager()).run(VIDEO)
    assert read_plan(job_dir)["generated_by"].startswith("ai:ollama")


def test_rewritten_document_carries_ai_structure(tmp_path):
    """المستند الناتج يعكس الصياغة: عنوان وملخّص وعناوين أقسام حقيقية."""
    from docx import Document
    out = tmp_path / "out"
    docx = make(out, rewrite=True).run(VIDEO)
    document = Document(str(docx))
    text = "\n".join(p.text for p in document.paragraphs)
    assert "عنوان من الذكاء الاصطناعي" in text
    assert "ملخّص تنفيذي" in text
    assert "قسم مُعاد صياغته" in text
    assert "القسم الأول" not in text, "بقيت عناوين المخطِّط الزمني"


def test_expected_source_reflects_settings():
    config = AppConfig()
    pipeline = VideoToDocPipeline(Path("."), config)
    assert pipeline._expected_plan_source() == "timeline"
    config.rewrite.enabled = True
    config.rewrite.provider = "anthropic"
    assert pipeline._expected_plan_source() == "ai:anthropic"
