"""عقود البيانات. أي تغيير غير متوافق يستوجب رفع ``schema_version`` + ADR."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import (
    BaseModel,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from version import CONFIG_SCHEMA_VERSION

SCHEMA_VERSION = CONFIG_SCHEMA_VERSION


class VideoMetadata(BaseModel):
    """ميتاداتا المصدر — فيديو أو صوت.

    ``has_video=False`` يصف ملفًا صوتيًا خالصًا (‏mp3، m4a، wav…). عندها
    تكون الأبعاد ومعدّل الإطارات أصفارًا بلا معنى، ويتخطّى الـ pipeline
    كشف المشاهد واستخراج الصور، فيخرج مستند نصّي منسّق بلا لقطات.
    القيود الموجبة تبقى مفروضة على الفيديو حيث تعني شيئًا.
    """
    schema_version: str = SCHEMA_VERSION
    filename: str
    path: Path
    duration_seconds: float = Field(..., gt=0)
    width: int = Field(..., ge=0)
    height: int = Field(..., ge=0)
    fps: float = Field(..., ge=0)
    codec: str
    has_audio: bool
    has_video: bool = True
    audio_sample_rate: Optional[int] = None
    # الدوران المصرَّح به في الحاوية؛ OpenCV يتجاهله فنصححه يدويًا
    rotation: int = 0
    stored_width: Optional[int] = None
    stored_height: Optional[int] = None
    nb_frames: Optional[int] = None

    @model_validator(mode="after")
    def _video_dimensions_are_positive(self) -> "VideoMetadata":
        """الأبعاد ومعدّل الإطارات إلزامية موجبة **للفيديو فقط**.

        إسقاط القيد كليًا كان سيسمح بفيديو أبعاده صفر يمرّ إلى كاشف
        المشاهد؛ وإبقاؤه مطلقًا يمنع الملفات الصوتية. الشرط هنا يفرّق.
        """
        if self.has_video and not (self.width > 0 and self.height > 0
                                   and self.fps > 0):
            raise ValueError(
                "أبعاد الفيديو ومعدّل إطاراته يجب أن تكون موجبة "
                "(أو اضبط has_video=False لمصدر صوتي).")
        return self

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


class TranscriptionCheckpoint(BaseModel):
    """تقدّمُ تفريغٍ لم يكتمل، محفوظًا على القرص.

    سبب وجوده مقيس: التفريغ 85٪ من زمن التشغيل، ومحاضرة ثلاث ساعات
    تستغرق نحو أربع ساعات ونصف على جهاز المستخدم. والاستئناف اليوم على
    مستوى **المرحلة** لا داخلها — أي أن انقطاع كهرباء أو إعادة تشغيل
    ويندوز في الساعة الرابعة يُضيع الأربع كلّها ويبدأ من الصفر.

    ما يُحفظ هو المقاطع **الخام قبل إعادة التشكيل**: الطيّ والقسمة
    يُطبَّقان على القائمة المجموعة في النهاية لا على كل جزء وحده، وإلا
    فاتت حلقةُ تكرار تقع على حدّ الاستئناف بالضبط.

    ``resume_at`` نهاية آخر مقطع محفوظ، وهي حدّ قطعٍ **دقيق**: الصوت
    يُقصّ عندها تمامًا، فلا يتكرّر مقطع ولا يسقط — وهو شرط ADR-010.
    """
    schema_version: str = SCHEMA_VERSION
    processing_fingerprint: str = ""
    #: مدة الصوت الكاملة — التقدّم بعد الاستئناف يُحسب عليها لا على البقية.
    audio_seconds: float = 0.0
    resume_at: float = 0.0
    segments: List[AudioSegment] = Field(default_factory=list)
    words: List[WordTimestamp] = Field(default_factory=list)


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
    # نص الشاشة عبر OCR (video/ocr.py) — اختياري، فارغ إن كان معطّلًا في
    # الإعداد أو Tesseract غير مثبَّت أو لم يُعثر على نص. حقل جديد بقيمة
    # افتراضية: مهمة قديمة تُستأنف من keyframes.json محفوظ قبله تُحمَّل
    # بلا مشكلة، وحقلها هذا فارغ ببساطة.
    ocr_text: str = ""


# ملاحظة: ``TimelineMatchResult`` من الإصدار 1.1 حُذف. حلّ محلّه
# ``DocumentPlan`` أدناه، وهو أغنى (أقسام وكتل لا عناصر مسطّحة) ويحمل
# ثوابت التحقق نفسها. لم يكن العقد القديم مُنتَجًا ولا مُستهلَكًا في أي
# وحدة — بقايا إعادة هيكلة.


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
    # نص الشاشة عبر OCR، منسوخ من KeyframeMetadata.ocr_text عند بناء
    # كتلة الشكل (document/planner.py وai/rewriter.py كلاهما). منفصل عن
    # ``caption`` عمدًا: الأول ما يُحسب من الكلام المصاحب، هذا ما يظهر
    # فعليًا مكتوبًا على الشاشة — مصدران مختلفان لا يجوز خلطهما.
    ocr_text: str = ""
    # مراجع المقاطع الأصلية — تضمن إمكانية التتبّع للنص الخام
    segment_ids: List[int] = Field(default_factory=list)
    # Provenance: نطاق المصدر الذي بُنيت منه الكتلة، إن أمكن.
    source_start: Optional[float] = None
    source_end: Optional[float] = None


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
    # تقرير جودة آخر بناء محفوظ داخل الخطة، اختياري للتوافق مع الخطط القديمة.
    quality_score: Optional[float] = None
    quality_warnings: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------
# الحزمة التعليمية — طبقة مشتقّة من الخطة والتفريغ، لا بديلة عنهما
# ---------------------------------------------------------------------
# ADR-018 · **كل مخرج تعليمي يُثبت مصدره أو يُسقَط.**
#
# مولّد الأسئلة العام يكتب سؤالًا معقولًا عن موضوع المحاضرة. وهذا لا
# يكفي في صفّ: المعلّم الذي لا يستطيع التحقّق من أن السؤال يخصّ ما قاله
# **هو** لن يستعمل الأداة مرّتين. ولذلك يحمل كل عنصر هنا ``segment_ids``
# تشير إلى مقاطع ``TranscriptionResult`` التي بُني منها، ويُفحص وجودها
# فعليًّا قبل القبول (``ai/study_verify``).
#
# والفائدة مزدوجة: ما لا يُثبت مصدره يُسقَط، وما يُقبل يصير قابلًا
# للنقر — من السؤال إلى لحظته في التسجيل.

class StudyItem(BaseModel):
    """أصلٌ مشترك: كل عنصر تعليمي يعرف من أين جاء."""
    #: مقاطع التفريغ التي بُني منها العنصر. فارغة = غير مُتتبَّع، ولا
    #: تُقبل إلا للعناصر المشتقّة إحصائيًّا من نصّ الشاشة.
    segment_ids: List[int] = Field(default_factory=list)
    source_start: Optional[float] = None
    source_end: Optional[float] = None


class LearningObjective(StudyItem):
    """هدف تعلّم واحد — ما يُفترض أن يقدر عليه الطالب بعد المحاضرة."""
    text: str


class GlossaryTerm(StudyItem):
    term: str
    definition: str = ""
    #: ``ai`` من نموذج لغوي · ``screen`` من نصّ الشاشة عبر OCR ·
    #: ``frequency`` من تكرار المصطلح في التفريغ. الأخيران يعملان بلا
    #: أي نموذج، وهما مسار من لا يملك واحدًا.
    origin: Literal["ai", "screen", "frequency"] = "ai"
    occurrences: int = 0


class QuizQuestion(StudyItem):
    kind: Literal["mcq", "true_false", "short"] = "mcq"
    question: str
    #: خيارات الاختيار من متعدد. فارغة لغير ``mcq``.
    options: List[str] = Field(default_factory=list)
    answer: str
    explanation: str = ""
    difficulty: Literal["easy", "medium", "hard"] = "medium"


class Flashcard(StudyItem):
    front: str
    back: str


class StudyPack(BaseModel):
    """حزمة المذاكرة كاملةً — تُحفظ في ``study.json`` بجوار ``plan.json``.

    حفظها مستقلّةً ليس ترتيبًا: توليدها هو الخطوة **الوحيدة** في
    البرنامج التي قد تكلّف مالًا أو دقائق انتظار عند مزوّد. وحفظها
    يعني أن ``--rebuild`` يُعيد تصيير كل الصيغ منها بلا استدعاء واحد
    للنموذج — كما يفعل ``plan.json`` مع المستند تمامًا.
    """
    schema_version: str = SCHEMA_VERSION
    title: str = ""
    #: ``none`` بلا نموذج · ``ai:<مزوّد>/<نموذج>`` · ``screen`` إحصائي
    generated_by: str = "none"
    objectives: List[LearningObjective] = Field(default_factory=list)
    glossary: List[GlossaryTerm] = Field(default_factory=list)
    questions: List[QuizQuestion] = Field(default_factory=list)
    flashcards: List[Flashcard] = Field(default_factory=list)
    #: ما أسقطته طبقة التحقّق وسببه. يُعرض في السجلّ وفي دليل المذاكرة.
    #: إخفاؤه يجعل حزمةً نصفُها مرفوض تبدو حزمةً صغيرة بلا سبب ظاهر.
    dropped: List[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.objectives or self.glossary
                    or self.questions or self.flashcards)
