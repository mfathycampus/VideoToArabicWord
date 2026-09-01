"""عقود البيانات. أي تغيير غير متوافق يستوجب رفع ``schema_version`` + ADR."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_serializer, field_validator

SCHEMA_VERSION = "1.2"


class VideoMetadata(BaseModel):
    schema_version: str = SCHEMA_VERSION
    filename: str
    path: Path
    duration_seconds: float = Field(..., gt=0)
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)
    fps: float = Field(..., gt=0)
    codec: str
    has_audio: bool
    audio_sample_rate: Optional[int] = None
    # الدوران المصرَّح به في الحاوية؛ OpenCV يتجاهله فنصححه يدويًا
    rotation: int = 0
    stored_width: Optional[int] = None
    stored_height: Optional[int] = None
    nb_frames: Optional[int] = None

    # JSON يحمل str؛ الذاكرة تحمل Path — كما تنص ملاحظة v1.1
    @field_validator("path", mode="before")
    @classmethod
    def _coerce_path(cls, v: Any) -> Path:
        return Path(v) if not isinstance(v, Path) else v

    @field_serializer("path")
    def _dump_path(self, v: Path) -> str:
        return str(v)


class WordTimestamp(BaseModel):
    word: str
    start: float
    end: float
    probability: Optional[float] = None


class AudioSegment(BaseModel):
    id: int
    start: float
    end: float
    text_raw: str
    text_clean: str
    words: List[WordTimestamp] = Field(default_factory=list)

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v: float, info) -> float:
        start = info.data.get("start")
        if start is not None and v < start:
            raise ValueError(f"end ({v}) يجب ألا يسبق start ({start})")
        return v


class TranscriptionResult(BaseModel):
    schema_version: str = SCHEMA_VERSION
    language: str
    full_text_raw: str
    full_text_clean: str
    segments: List[AudioSegment] = Field(default_factory=list)
    words: List[WordTimestamp] = Field(default_factory=list)
    engine: Dict[str, Any] = Field(default_factory=dict)


class KeyframeMetadata(BaseModel):
    image_id: int
    timestamp: float
    filename: str
    scene_id: int
    change_score: float
    width: int
    height: int
    selection_reason: str
    checksum: Optional[str] = None


class DocumentItem(BaseModel):
    """عنصر واحد في المستند النهائي.

    تغيير جوهري عن v1.0/v1.1: العنصر الآن **مدفوع بالنص** لا بالصورة.
    كل ``AudioSegment`` يظهر في عنصر واحد بالضبط، والصور تُحقن كعناصر
    مستقلة عند طوابعها الزمنية. هذا هو ما يمنع ضياع الكلام وتكراره.
    """
    item_id: int
    kind: Literal["image", "text"]
    timestamp: float
    # للصور
    image_id: Optional[int] = None
    image_filename: Optional[str] = None
    # للنصوص
    segment_id: Optional[int] = None
    text: str = ""


class TimelineMatchResult(BaseModel):
    schema_version: str = SCHEMA_VERSION
    items: List[DocumentItem] = Field(default_factory=list)
    # ثوابت تحقق تُفحص في Quality Gates
    total_segments_in: int = 0
    total_segments_out: int = 0
    total_keyframes_in: int = 0
    total_keyframes_out: int = 0


# ---------------------------------------------------------------------
# خطة المستند — الطبقة التي تفصل *بنية* الوثيقة عن *تصييرها*
# ---------------------------------------------------------------------

class DocumentBlock(BaseModel):
    """كتلة محتوى داخل قسم."""
    kind: Literal["paragraph", "figure", "bullets", "quote"]
    text: str = ""
    bullets: List[str] = Field(default_factory=list)
    timestamp: Optional[float] = None
    image_id: Optional[int] = None
    image_filename: Optional[str] = None
    caption: str = ""
    # مراجع المقاطع الأصلية — تضمن إمكانية التتبّع للنص الخام
    segment_ids: List[int] = Field(default_factory=list)


class DocumentSection(BaseModel):
    title: str
    level: int = 1
    summary: str = ""
    start_timestamp: Optional[float] = None
    blocks: List[DocumentBlock] = Field(default_factory=list)


class DocumentPlan(BaseModel):
    """بنية الوثيقة النهائية، مستقلة عن صيغة الإخراج.

    تُنتَج إما من مُخطِّط زمني بسيط أو من وحدة إعادة الصياغة بالذكاء
    الاصطناعي. المولّد يصيّرها على القالب دون أن يعرف مصدرها — فصلٌ
    يسمح باستبدال أيّ من الطرفين وحده.
    """
    schema_version: str = SCHEMA_VERSION
    title: str
    subtitle: str = ""
    abstract: str = ""
    key_points: List[str] = Field(default_factory=list)
    sections: List[DocumentSection] = Field(default_factory=list)
    generated_by: str = "timeline"     # timeline | ai:<provider>/<model>
    # ثوابت تحقق: تُفحص في بوابات الجودة
    total_segments_in: int = 0
    total_segments_out: int = 0
    total_figures_in: int = 0
    total_figures_out: int = 0
