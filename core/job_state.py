"""حالة المهمة والاستئناف — الوحدة المفقودة كليًا من الحزمة الأصلية.

المواصفة v1.0 تُفصّل Job State (قسم 8) والاستئناف (قسم 24) وعقود البيانات
(أقسام 4–7)، لكن الحزمة التنفيذية **لا تكتب أيًا منها**: ``pipeline.py``
يمرّر كائنات في الذاكرة فقط. بلا هذه الوحدة:

    * لا استئناف — مستخدم يفرّغ محاضرتين على CPU ويفشل عند 90% يبدأ من الصفر
    * لا اختبار مستقل لأي مرحلة — لا توجد artifacts لتغذيتها
    * لا تشخيص بعد الانهيار

لذلك تنتقل هذه الوحدة من Sprint 6 إلى Sprint 1: هي بنية تحتية لا ميزة.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from core.exceptions import ArtifactMissingError
from utils.logger import logger

SCHEMA_VERSION = "1.3"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERABLE = "recoverable"


class Stage(str, Enum):
    VALIDATION = "validation"
    METADATA = "metadata"
    AUDIO_EXTRACTION = "audio_extraction"
    TRANSCRIPTION = "transcription"
    SCENE_DETECTION = "scene_detection"
    KEYFRAMES = "keyframes"
    MATCHING = "matching"
    DOCUMENT = "document"


# نطاقات التقدّم لكل مرحلة — مصدر وحيد للحقيقة
STAGE_PROGRESS: Dict[Stage, tuple[float, float]] = {
    Stage.VALIDATION: (0.0, 5.0),
    Stage.METADATA: (5.0, 10.0),
    Stage.AUDIO_EXTRACTION: (10.0, 20.0),
    Stage.TRANSCRIPTION: (20.0, 55.0),
    Stage.SCENE_DETECTION: (55.0, 75.0),
    Stage.KEYFRAMES: (75.0, 85.0),
    Stage.MATCHING: (85.0, 92.0),
    Stage.DOCUMENT: (92.0, 100.0),
}


def file_checksum(path: Path, chunk_size: int = 1 << 20) -> str:
    """SHA-256 مقصوص إلى 16 حرفًا — كافٍ للتحقق من الصلاحية لا للأمان."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def atomic_write_text(path: Path, content: str) -> None:
    """كتابة ذرّية: ملف مؤقت ثم ``os.replace``.

    بدونها، انقطاع كهرباء أثناء حفظ الحالة يترك ملف JSON مقطوعًا،
    فيفشل الاستئناف بالضبط في الحالة التي وُجد من أجلها.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class ArtifactRef(BaseModel):
    filename: str
    checksum: str
    created_at: str


class JobState(BaseModel):
    schema_version: str = SCHEMA_VERSION
    job_id: str
    video_path: str
    status: JobStatus = JobStatus.QUEUED
    stage: Optional[Stage] = None
    progress: float = 0.0
    completed_stages: list[Stage] = Field(default_factory=list)
    artifacts: Dict[str, ArtifactRef] = Field(default_factory=dict)
    retry_count: int = 0
    max_retries: int = 3
    last_error: Optional[str] = None
    last_error_timestamp: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class JobManager:
    """يملك دورة حياة المهمة على القرص.

    الاستخدام:
        job = JobManager.create_or_resume(job_dir, video_path)
        if not job.is_stage_complete(Stage.TRANSCRIPTION):
            result = transcribe(...)
            job.save_artifact("transcription", "transcription.json",
                              result.model_dump_json(indent=2))
            job.complete_stage(Stage.TRANSCRIPTION)
        else:
            result = TranscriptionResult(**job.load_artifact("transcription"))
    """

    STATE_FILENAME = "job_state.json"

    def __init__(self, job_dir: Path, state: JobState) -> None:
        self.job_dir = job_dir
        self.state = state

    # ------------------------------------------------------------------
    @classmethod
    def create_or_resume(cls, job_dir: Path, video_path: Path,
                         job_id: Optional[str] = None) -> "JobManager":
        job_dir.mkdir(parents=True, exist_ok=True)
        state_path = job_dir / cls.STATE_FILENAME

        if state_path.exists():
            try:
                stored = json.loads(state_path.read_text(encoding="utf-8"))
                # حالة من إصدار أقدم: أسماء النواتج وبنيتها قد تكون تغيّرت،
                # فاستئنافها ينهار على ناتج لم يعد له وجود. نبدأ من الصفر
                # بدل الانهيار (قاعدة رفع schema_version).
                if stored.get("schema_version") != SCHEMA_VERSION:
                    logger.info(
                        f"حالة مهمة بإصدار مختلف "
                        f"({stored.get('schema_version')} ≠ {SCHEMA_VERSION}) "
                        "— إعادة البدء من الصفر.")
                    raise ValueError("schema version mismatch")
                state = JobState(**stored)
                if state.video_path == str(video_path):
                    manager = cls(job_dir, state)
                    manager._invalidate_missing_artifacts()
                    return manager
            except Exception:
                # حالة تالفة: نبدأ من جديد بدل الانهيار
                pass

        state = JobState(
            job_id=job_id or datetime.now().strftime("%Y%m%d-%H%M%S"),
            video_path=str(video_path),
        )
        manager = cls(job_dir, state)
        manager.persist()
        return manager

    # ------------------------------------------------------------------
    def persist(self) -> None:
        self.state.updated_at = datetime.now(timezone.utc).isoformat()
        atomic_write_text(
            self.job_dir / self.STATE_FILENAME,
            self.state.model_dump_json(indent=2),
        )

    def _invalidate_missing_artifacts(self) -> None:
        """يُسقط أي مرحلة اختفى أو تغيّر ناتجها على القرص.

        هذا ما يجعل الاستئناف *آمنًا*: وجود اسم المرحلة في
        ``completed_stages`` لا يكفي — يجب أن يوجد الملف وأن يطابق checksum.
        """
        stage_artifact = {
            Stage.METADATA: "metadata",
            Stage.TRANSCRIPTION: "transcription",
            Stage.SCENE_DETECTION: "scenes",
            Stage.KEYFRAMES: "keyframes",
            Stage.MATCHING: "plan",
            Stage.DOCUMENT: "document",
        }
        invalid: set[Stage] = set()
        for stage, key in stage_artifact.items():
            if stage not in self.state.completed_stages:
                continue
            ref = self.state.artifacts.get(key)
            if ref is None:
                invalid.add(stage)
                continue
            path = self.job_dir / ref.filename
            if not path.exists() or file_checksum(path) != ref.checksum:
                invalid.add(stage)
                self.state.artifacts.pop(key, None)

        if invalid:
            # إسقاط المرحلة التالفة وكل ما يليها (التبعية تسلسلية)
            order = list(Stage)
            earliest = min(order.index(s) for s in invalid)
            self.state.completed_stages = [
                s for s in self.state.completed_stages if order.index(s) < earliest
            ]
            self.persist()

    # ------------------------------------------------------------------
    def is_stage_complete(self, stage: Stage) -> bool:
        return stage in self.state.completed_stages

    def begin_stage(self, stage: Stage) -> None:
        self.state.stage = stage
        self.state.status = JobStatus.RUNNING
        self.state.progress = STAGE_PROGRESS[stage][0]
        self.persist()

    def complete_stage(self, stage: Stage) -> None:
        if stage not in self.state.completed_stages:
            self.state.completed_stages.append(stage)
        self.state.progress = STAGE_PROGRESS[stage][1]
        self.persist()

    def stage_progress(self, stage: Stage, fraction: float) -> float:
        """يحوّل تقدّمًا داخليًا [0,1] إلى النسبة الكلية للمهمة."""
        low, high = STAGE_PROGRESS[stage]
        return low + max(0.0, min(1.0, fraction)) * (high - low)

    # ------------------------------------------------------------------
    def save_artifact(self, key: str, filename: str, content: str) -> Path:
        path = self.job_dir / filename
        atomic_write_text(path, content)
        self.state.artifacts[key] = ArtifactRef(
            filename=filename,
            checksum=file_checksum(path),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.persist()
        return path

    def register_artifact(self, key: str, path: Path) -> None:
        """يسجّل ناتجًا كُتب خارجيًا (صورة، docx، wav)."""
        self.state.artifacts[key] = ArtifactRef(
            filename=str(path.relative_to(self.job_dir))
            if path.is_relative_to(self.job_dir) else str(path),
            checksum=file_checksum(path),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.persist()

    def has_artifact(self, key: str) -> bool:
        ref = self.state.artifacts.get(key)
        return bool(ref) and (self.job_dir / ref.filename).exists()

    def load_artifact(self, key: str) -> Any:
        """يقرأ ناتجًا محفوظًا.

        ``KeyError`` هنا يعني حالة مهمة تشير إلى ناتج غير موجود — عطل
        استئناف لا عطل بيانات. نرفعه كخطأ مفهوم بدل تسريب KeyError خام
        إلى واجهة المستخدم.
        """
        ref = self.state.artifacts.get(key)
        if ref is None:
            raise ArtifactMissingError(
                f"الناتج «{key}» غير مسجّل في حالة المهمة. "
                "احذف مجلد المهمة وأعد التشغيل.")
        path = self.job_dir / ref.filename
        if not path.exists():
            raise ArtifactMissingError(f"ملف الناتج مفقود: {path.name}")
        return json.loads(path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    def fail(self, error: str) -> None:
        self.state.status = (
            JobStatus.RECOVERABLE
            if self.state.retry_count < self.state.max_retries
            else JobStatus.FAILED
        )
        self.state.retry_count += 1
        self.state.last_error = error[:2000]
        self.state.last_error_timestamp = datetime.now(timezone.utc).isoformat()
        self.persist()

    def cancel(self) -> None:
        self.state.status = JobStatus.CANCELLED
        self.persist()

    def finish(self) -> None:
        self.state.status = JobStatus.COMPLETED
        self.state.stage = None
        self.state.progress = 100.0
        self.state.last_error = None
        self.persist()
