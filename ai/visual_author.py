"""المؤلّف المرئيّ: مستندٌ يُكتب من الشاشة والكلام معًا.

**المشكلة التي يحلّها.** إعادة الصياغة النصّية (``ai/rewriter``) لا ترى إلا
التفريغ ونصّ OCR. وعلى تسجيل شاشة عامّيّ حقيقي («متابعة تحضير
المعلمين»، ‏PowerSchool) كان التفريغ 60٪ من الكلام وفيه «المتاجر» بدل
«المدارس» و«اشتركوا في القناة» هلوسةً — والنموذج يبني على ذلك. بينما
الشاشة نفسها تقول بوضوح: ‏Curriculum & Instruction ← Admin ← Lesson
Feedback ← اختيار المدرسة. **في تسجيل الشاشة، الصورة هي المصدر الأوثق.**

وتسجيلات بلا صوت إطلاقًا (خمسة من ستّين فيديو في مجلد المستخدم) لم
يكن لها مسار أصلًا: الصياغة ترفض «لا يوجد نص»، فيخرج المستند صورًا
بتعليق «لقطة عند 00:00:04».

**ما تفعله.** ترسل اللقطات المختارة **صورًا** إلى نموذج مرئي، كلّ لقطة
مع الكلام المصاحب لها زمنيًّا، وتطلب دليلًا منظّمًا: أقسام، وخطوات
إجرائية مرقّمة تسمّي الأزرار كما تظهر، وتعليقات تصف ما في كل لقطة،
واستبعاد اللقطات المكرّرة وغير المتعلّقة **بإعلانٍ صريح**.

الضمانات المفروضة برمجيًّا (لا بالموجّه وحده):
    * كل لقطة إمّا في المستند مرّة واحدة أو في ``figures_omitted`` —
      ولا ثالث (‏``assert_lossless``).
    * كل مقطع تفريغ يُنسب إلى قسم واحد بزمنه — التتبّع باقٍ.
    * معرّف لقطة لم يُرسَل يُهمَل؛ لقطة لم يذكرها النموذج تُعاد إلى
      موضعها الزمني بدل أن تضيع.
    * فشل النداء يرفع ``RewriteUnavailableError``، فيسقط الـpipeline إلى
      الصياغة النصّية ثم إلى المخطّط الزمني.
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from ai.providers import (
    LLMProvider,
    ResponseTruncatedError,
    RewriteUnavailableError,
    complete_with_retry,
    dump_failed_response,
    image_part,
    text_part,
)
from ai.rewriter import ANONYMIZE_NOTE, _extract_json, _is_degenerate_title
from config.schemas import (
    AudioSegment,
    DocumentBlock,
    DocumentPlan,
    DocumentSection,
    KeyframeMetadata,
    TranscriptionResult,
)
from utils.logger import logger
from utils.timestamps import seconds_to_display

SYSTEM_PROMPT = """أنت كاتب أدلّة تقنية ومحرّر عربي محترف. أمامك تسجيل مرئي مُقدَّم لك خطًّا زمنيًّا:
لقطات شاشة مرقّمة بأزمنتها، وبينها التفريغ الآلي للكلام المصاحب.

مهمّتك: كتابة مستند عربي احترافي جاهز للتوزيع على المعلّمين والموظّفين.

مصادر الحقيقة، بالترتيب:
1. **اللقطات** هي المرجع لأسماء الأنظمة والقوائم والأزرار والحقول والأرقام الظاهرة.
2. **الكلام** يشرح الغرض والترتيب. لكنه تفريغ آلي لكلام قد يكون عاميًّا، وفيه أخطاء سمعية
   (مثل «المتاجر» بدل «المدارس»، «التخدير» بدل «التحضير») وعبارات مُختلَقة لا صلة لها
   بالموضوع (مثل «اشتركوا في القناة»، «ترجمة نانسي قنقر»). ما يخالف الشاشة أو الموضوع أهمِله.
3. لا تخترع: لا ميزة ولا رقم ولا اسم ولا خطوة لا تظهر في لقطة ولا تُفهم من الكلام.
   ما لا تتأكّد منه احذفه، أو اذكره في ملاحظة (note) بصيغة حذرة.

حدّد نوع المستند:
- "procedure": شرح كيفية أداء مهمّة في نظام أو برنامج (الأغلب في تسجيلات الشاشة).
  اكتب الأقسام مهامَّ («الوصول إلى صفحة متابعة التحضير»)، وكل خطوة فعلًا واحدًا بصيغة
  الأمر («اضغط»، «اختر»، «اكتب»)، واسم العنصر كما يظهر على الشاشة بين «» ويبقى
  الاسم اللاتيني لاتينيًّا: اضغط «Admin» ثم اختر «Lesson Feedback».
  ضع اللقطة بعد الخطوة التي توضّحها مباشرة.
- "lecture": شرح مفاهيم أو عرض شرائح — فقرات مترابطة ونقاط.
- "meeting": اجتماع — ملخّص، ثم القرارات والمهام نقاطًا.

اللقطات:
- استعمل اللقطات المفيدة فقط، وكل لقطة مرّة واحدة على الأكثر.
- استبعد المكرّرة والمتطابقة تقريبًا، وشاشات التحميل، والنوافذ الخارجة عن الموضوع،
  واذكر أرقامها في "omit".
- التعليق (caption) يصف ما تُظهره اللقطة وما ينبغي أن يلاحظه القارئ فيها، في 6–16 كلمة،
  لا «لقطة شاشة» ولا الزمن.
- "screen_text": أهمّ النصوص المقروءة فعلًا في اللقطة (عناوين، أزرار، قوائم) مفصولة بـ |.
- "focus" (اختياري): إن كان المهمّ في اللقطة جزءًا منها (قائمة منسدلة، نافذة، لوحة جانبية)،
  أعطِ مستطيلًا يحيط به مع شيء من سياقه: [يسار، أعلى، يمين، أسفل] كنِسَب من 0 إلى 1 من
  عرض الصورة وارتفاعها. يُقصّ المستطيل ويُكبَّر في المستند فيُقرأ ما فيه. لا تجعله أصغر
  من ثلث العرض أو ربع الارتفاع. احذفه حين تهمّ الشاشة كاملة.

الأسلوب: عربية فصحى معاصرة واضحة ومختصرة، بلا حشو ولا مخاطبة للقارئ عن المهمّة نفسها
ولا ذكر للتسجيل أو المتحدّث («يقول المتحدّث»، «في هذا الفيديو»).
العنوان يصف المهمّة أو الموضوع في 3–8 كلمات. عناوين الأقسام مختلفة ومحدّدة.

أعِد **JSON فقط** بهذا الشكل:
{"doc_type": "procedure",
 "title": "عنوان المستند",
 "abstract": "ملخّص في 2–3 جمل: ماذا يتعلّم القارئ ومتى يحتاجه",
 "key_points": ["نقطة", "نقطة"],
 "sections": [
   {"title": "عنوان القسم", "summary": "جملة اختيارية",
    "blocks": [
      {"type": "step", "text": "اضغط «Admin» في الشريط العلوي."},
      {"type": "figure", "id": 3, "caption": "قائمة Admin مفتوحة وفيها خيار Lesson Feedback",
       "screen_text": "Admin | Lesson Feedback | Curriculum", "focus": [0.3, 0.0, 0.75, 0.45]},
      {"type": "paragraph", "text": "..."},
      {"type": "bullets", "items": ["...", "..."]},
      {"type": "note", "text": "تنبيه أو ملاحظة مهمّة"}
    ]}
 ],
 "omit": [5]}"""

CONTINUATION_NOTE = (
    "هذا جزء من تسجيل أطول (الجزء {part} من {total}). اكتب أقسام هذا الجزء "
    "وحده، ولا تكرّر ما يُفهم أنه شُرح قبله. اترك title وabstract وkey_points "
    "فارغة إن لم يكن هذا الجزء الأول.\n\n")

OUTLINE_PROMPT = """أنت محرّر عربي محترف. أمامك عناوين أقسام دليل وملخّصاتها.

أعِد **JSON فقط**:
{"title": "عنوان المستند كاملًا في 3-8 كلمات",
 "abstract": "ملخّص في 2-3 جمل: ماذا يتعلّم القارئ ومتى يحتاجه",
 "key_points": ["نقطة", "نقطة", "نقطة"]}

لا تضف معلومات غير موجودة. 3-6 نقاط رئيسية."""

#: أنواع الكتل التي يقبلها المخطّط من النموذج. غيرها يُعامل فقرة.
_BLOCK_KINDS = {"step", "paragraph", "bullets", "note", "figure"}
_DOC_TYPES = {"procedure", "lecture", "meeting"}


@dataclass
class VisualAuthorConfig:
    #: أقصى لقطات في النداء الواحد. تسجيلٌ أطول يُقسَّم أجزاءً زمنية.
    #: كان 20: على تشغيل حقيقي (81 دقيقة، 128 لقطة) قُطع ردّ الجزء الأول
    #: عند سقف الرموز فسقط الوضع المرئي كلّه. 12 لقطة تُبقي الردّ ضمن
    #: السقف، والجزء الذي يُقطع رغم ذلك يُقسَم نصفين (``_author_chunk``).
    max_images_per_call: int = 12
    #: عرض الصورة المُرسَلة. ‏1400px يُبقي نصّ الواجهات (≈11px) مقروءًا
    #: ويكلّف نحو 1400 رمز للصورة لدى Claude.
    image_max_width: int = 1400
    image_quality: int = 80
    #: سقف نصّ التفريغ في الجزء الواحد.
    max_transcript_chars: int = 14000
    timeout_seconds: int = 600
    #: كان 8000: دليلٌ عربيّ لعشرين لقطة بتعليقاتها ونصوص شاشتها يتجاوزه.
    max_tokens: int = 16000
    #: أصغر جزء يُقسَم عند القطع أو فساد الردّ. أصغر منه: الفشل نهائي.
    min_images_to_split: int = 4
    glossary: str = ""
    anonymize_names: bool = False


@dataclass
class _Chunk:
    keyframes: List[KeyframeMetadata]
    segments: List[AudioSegment]
    start: float
    end: float


@dataclass
class _Authored:
    """ناتج جزء واحد بعد التحقّق."""
    doc_type: str = ""
    title: str = ""
    abstract: str = ""
    key_points: List[str] = field(default_factory=list)
    sections: List[dict] = field(default_factory=list)
    omitted: List[int] = field(default_factory=list)


#: أصغر مستطيل تركيز كنسبة من الصورة. أصغر منه يُكبَّر فيتشوّش، ويفقد
#: القارئ موضع العنصر من الشاشة.
MIN_FOCUS_WIDTH = 0.30
MIN_FOCUS_HEIGHT = 0.25
#: هامش سياق حول المستطيل، ونسبة ارتفاع إلى عرض لا يتجاوزها القصّ —
#: قائمةٌ طويلة ضيّقة تُوسَّع عرضًا فلا تصير شريطًا عموديًّا في الصفحة.
FOCUS_PAD = 0.02
MAX_FOCUS_ASPECT = 1.1


def _focus_box(value) -> Optional[Tuple[float, float, float, float]]:
    """يطبّع مستطيل التركيز أو يعيد ``None`` إن كان غير صالح أو بلا فائدة."""
    try:
        left, top, right, bottom = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    left, right = sorted((max(0.0, min(1.0, left)), max(0.0, min(1.0, right))))
    top, bottom = sorted((max(0.0, min(1.0, top)), max(0.0, min(1.0, bottom))))
    left, top = max(0.0, left - FOCUS_PAD), max(0.0, top - FOCUS_PAD)
    right, bottom = min(1.0, right + FOCUS_PAD), min(1.0, bottom + FOCUS_PAD)

    def widen(lo: float, hi: float, minimum: float) -> Tuple[float, float]:
        if hi - lo >= minimum:
            return lo, hi
        centre = (lo + hi) / 2
        lo = max(0.0, min(1.0 - minimum, centre - minimum / 2))
        return lo, lo + minimum

    left, right = widen(left, right, MIN_FOCUS_WIDTH)
    top, bottom = widen(top, bottom, MIN_FOCUS_HEIGHT)
    if right - left > 0.92 and bottom - top > 0.92:
        return None                      # الصورة كلّها: لا قصّ
    return (left, top, right, bottom)


def _crop_focus(images_dir: Path, filename: str,
                box: Tuple[float, float, float, float]) -> Optional[str]:
    """يقصّ منطقة التركيز من اللقطة ويحفظها بجوارها. الأصل لا يُمسّ."""
    try:
        from PIL import Image

        source = images_dir / filename
        with Image.open(source) as image:
            width, height = image.size
            left, top, right, bottom = (box[0] * width, box[1] * height,
                                        box[2] * width, box[3] * height)
            # نسبة الارتفاع إلى العرض بالبكسل لا بالنسبة: الشاشة عريضة.
            if (bottom - top) > MAX_FOCUS_ASPECT * (right - left):
                need = (bottom - top) / MAX_FOCUS_ASPECT
                centre = (left + right) / 2
                left = max(0.0, min(width - need, centre - need / 2))
                right = min(float(width), left + need)
            crop = image.convert("RGB").crop(
                (int(left), int(top), int(round(right)), int(round(bottom))))
            key = "-".join(f"{v:.2f}" for v in box)
            name = f"{Path(filename).stem}_focus_{key}.jpg"
            crop.save(images_dir / name, "JPEG", quality=88)
        return name
    except Exception as exc:
        logger.warning(f"تعذّر قصّ منطقة التركيز من {filename}: {exc}")
        return None


def _seg_text(segment: AudioSegment) -> str:
    return (segment.text_clean or segment.text_raw or "").strip()


class VisualAuthor:
    """يبني ``DocumentPlan`` من اللقطات والتفريغ عبر نموذج مرئي."""

    def __init__(self, provider: LLMProvider,
                 config: Optional[VisualAuthorConfig] = None) -> None:
        self.provider = provider
        self.config = config or VisualAuthorConfig()
        self.images_sent = 0

    # ------------------------------------------------------------------
    @staticmethod
    def usable(provider: LLMProvider, keyframes: List[KeyframeMetadata]) -> bool:
        return bool(keyframes) and bool(getattr(provider, "supports_vision", False))

    def build_plan(self, transcript: TranscriptionResult,
                   keyframes: List[KeyframeMetadata], images_dir: Path,
                   fallback_title: str, subtitle: str = "",
                   duration_seconds: float = 0.0,
                   progress_callback: Optional[Callable[[float, str], None]] = None,
                   ) -> DocumentPlan:
        if not keyframes:
            raise RewriteUnavailableError("لا لقطات للمؤلّف المرئي.")
        keyframes = sorted(keyframes, key=lambda k: k.timestamp)
        segments = [s for s in transcript.segments if _seg_text(s)]
        chunks = self._chunks(keyframes, segments, duration_seconds)

        authored: List[_Authored] = []
        for index, chunk in enumerate(chunks, start=1):
            if progress_callback:
                progress_callback((index - 1) / (len(chunks) + 1),
                                  f"كتابة المستند من اللقطات: الجزء {index} من {len(chunks)}")
            authored.append(self._author_chunk(
                chunk, images_dir, fallback_title, index, len(chunks)))

        head = authored[0]
        title, abstract, key_points = head.title, head.abstract, head.key_points
        if len(authored) > 1:
            title, abstract, key_points = self._outline(authored, fallback_title)
        if not title or _is_degenerate_title(title):
            title = fallback_title

        plan = self._assemble(authored, keyframes, list(transcript.segments),
                              images_dir=images_dir)
        plan.title = title
        plan.subtitle = subtitle or self._subtitle(head.doc_type)
        plan.abstract = abstract
        plan.key_points = key_points[:6]
        plan.generated_by = (f"ai:{self.provider.info.name}/"
                             f"{getattr(self.provider, 'model', '') or 'default'}"
                             "+vision")
        if progress_callback:
            progress_callback(1.0, "اكتملت كتابة المستند")
        return plan

    # ------------------------------------------------------------------
    @staticmethod
    def _subtitle(doc_type: str) -> str:
        return {"procedure": "دليل إجرائي مصوّر",
                "meeting": "محضر اجتماع",
                "lecture": "ملخّص محاضرة"}.get(doc_type, "")

    def _chunks(self, keyframes: List[KeyframeMetadata],
                segments: List[AudioSegment], duration: float) -> List[_Chunk]:
        """أجزاء زمنية متتالية، كلٌّ ضمن سقف اللقطات والنصّ."""
        per_call = max(1, self.config.max_images_per_call)
        groups: List[List[KeyframeMetadata]] = []
        current: List[KeyframeMetadata] = []
        chars = 0
        seg_iter = sorted(segments, key=lambda s: s.start)
        for keyframe in keyframes:
            span_chars = sum(len(_seg_text(s)) for s in seg_iter
                             if (current and current[-1].timestamp <= s.start < keyframe.timestamp))
            if current and (len(current) >= per_call
                            or chars + span_chars > self.config.max_transcript_chars):
                groups.append(current)
                current, chars = [], 0
            current.append(keyframe)
            chars += span_chars
        if current:
            groups.append(current)

        end_time = max([duration] + [s.end for s in segments]
                       + [k.timestamp for k in keyframes])
        chunks: List[_Chunk] = []
        for index, group in enumerate(groups):
            start = 0.0 if index == 0 else group[0].timestamp
            end = groups[index + 1][0].timestamp if index + 1 < len(groups) else end_time + 1
            chunk_segments = [s for s in segments if start <= s.start < end]
            chunks.append(_Chunk(group, chunk_segments, start, end))
        return chunks

    def _encode(self, path: Path) -> Optional[str]:
        try:
            from PIL import Image

            with Image.open(path) as image:
                image = image.convert("RGB")
                if image.width > self.config.image_max_width:
                    ratio = self.config.image_max_width / image.width
                    image = image.resize(
                        (self.config.image_max_width, max(1, int(image.height * ratio))))
                buffer = io.BytesIO()
                image.save(buffer, "JPEG", quality=self.config.image_quality)
            return base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception as exc:
            logger.warning(f"تعذّر تجهيز اللقطة {path.name}: {exc}")
            return None

    def _context(self, title: str, has_speech: bool) -> str:
        lines = [f"- اسم الملف: {title}"]
        glossary = " ".join((self.config.glossary or "").split())
        if self.config.anonymize_names:
            from video.redact import scrub_person_terms

            glossary = scrub_person_terms(glossary)
        if glossary:
            lines.append(f"- مصطلحات محتملة: {glossary[:400]}")
        if not has_speech:
            lines.append("- التسجيل بلا كلام مسموع: اعتمد على اللقطات وحدها.")
        text = "السياق:\n" + "\n".join(lines) + "\n\n"
        if self.config.anonymize_names:
            text += ANONYMIZE_NOTE
        return text

    def _parts(self, chunk: _Chunk, images_dir: Path, title: str,
               part: int, total: int) -> Tuple[list, Dict[int, KeyframeMetadata]]:
        """الخطّ الزمني: كلام، ثم لقطة، ثم كلام… بترتيب وقوعها."""
        parts: list = []
        intro = self._context(title, bool(chunk.segments))
        if total > 1:
            intro += CONTINUATION_NOTE.format(part=part, total=total)
        intro += "الخطّ الزمني:\n"
        parts.append(text_part(intro))

        sent: Dict[int, KeyframeMetadata] = {}
        events: List[Tuple[float, int, object]] = [
            (s.start, 1, s) for s in chunk.segments]
        events += [(k.timestamp, 0, k) for k in chunk.keyframes]
        events.sort(key=lambda e: (e[0], e[1]))

        speech: List[str] = []

        def flush() -> None:
            if speech:
                parts.append(text_part("الكلام: " + " ".join(speech)))
                speech.clear()

        for _, kind, item in events:
            if kind == 1:
                speech.append(f"[{seconds_to_display(item.start)}] {_seg_text(item)}")
                continue
            data = self._encode(images_dir / item.filename)
            if data is None:
                continue
            flush()
            parts.append(text_part(
                f"[لقطة {item.image_id} · {seconds_to_display(item.timestamp)}]"))
            parts.append(image_part(data))
            sent[item.image_id] = item
        flush()
        if not sent:
            raise RewriteUnavailableError("تعذّر تجهيز أيّ لقطة للإرسال.")
        return parts, sent

    def _author_chunk(self, chunk: _Chunk, images_dir: Path, title: str,
                      part: int, total: int) -> _Authored:
        """يكتب جزءًا واحدًا — ويقسمه نصفين إن قُطع ردّه أو فسد.

        رُصد على تشغيل حقيقي: ردّ الجزء الأول (20 لقطة) «ليس JSON»،
        فسقط الوضع المرئي كلّه إلى الصياغة النصّية. الجزء الأصغر ينتج
        ردًّا أقصر، فالقسمة علاجٌ مباشر لا يحتاج سقفًا أعلى من الخدمة.
        """
        try:
            return self._author_once(chunk, images_dir, title, part, total)
        except RewriteUnavailableError as exc:
            splittable = isinstance(exc, ResponseTruncatedError) or getattr(
                exc, "malformed", False)
            if not splittable or len(chunk.keyframes) < self.config.min_images_to_split:
                raise
            first, second = self._split(chunk)
            logger.warning(
                f"الجزء {part}: {exc} — يُقسَم إلى {len(first.keyframes)} "
                f"و{len(second.keyframes)} لقطة ويُعاد.")
            a = self._author_chunk(first, images_dir, title, part, total)
            b = self._author_chunk(second, images_dir, title, part, total)
            return _Authored(
                doc_type=a.doc_type or b.doc_type,
                title=a.title or b.title, abstract=a.abstract or b.abstract,
                key_points=a.key_points or b.key_points,
                sections=a.sections + b.sections,
                omitted=a.omitted + b.omitted)

    @staticmethod
    def _split(chunk: _Chunk) -> Tuple[_Chunk, _Chunk]:
        middle = len(chunk.keyframes) // 2
        boundary = chunk.keyframes[middle].timestamp
        first = _Chunk(chunk.keyframes[:middle],
                       [s for s in chunk.segments if s.start < boundary],
                       chunk.start, boundary)
        second = _Chunk(chunk.keyframes[middle:],
                        [s for s in chunk.segments if s.start >= boundary],
                        boundary, chunk.end)
        return first, second

    def _author_once(self, chunk: _Chunk, images_dir: Path, title: str,
                     part: int, total: int) -> _Authored:
        parts, sent = self._parts(chunk, images_dir, title, part, total)
        self.images_sent += len(sent)
        label = f"visual_part{part}_{seconds_to_display(chunk.start)}"
        try:
            raw = complete_with_retry(
                self.provider, SYSTEM_PROMPT, parts,
                max_tokens=self.config.max_tokens,
                timeout=self.config.timeout_seconds)
        except ResponseTruncatedError as exc:
            dump_failed_response(label, exc.partial, str(exc))
            raise
        data = _extract_json(raw)
        if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
            dump_failed_response(label, raw, "ليس JSON بالشكل المطلوب")
            error = RewriteUnavailableError(
                "ردّ المؤلّف المرئي ليس JSON بالشكل المطلوب.")
            error.malformed = True
            raise error
        return self._validate(data, sent, chunk)

    # ------------------------------------------------------------------
    @staticmethod
    def _clean(value) -> str:
        return " ".join(str(value or "").split())

    def _validate(self, data: dict, sent: Dict[int, KeyframeMetadata],
                  chunk: _Chunk) -> _Authored:
        """يطبّع ردّ النموذج ويفرض قيود اللقطات."""
        used: set[int] = set()
        sections: List[dict] = []
        for raw_section in data.get("sections") or []:
            if not isinstance(raw_section, dict):
                continue
            blocks: List[dict] = []
            for raw in raw_section.get("blocks") or []:
                if not isinstance(raw, dict):
                    continue
                kind = str(raw.get("type") or "paragraph").strip().lower()
                if kind not in _BLOCK_KINDS:
                    kind = "paragraph"
                if kind == "figure":
                    try:
                        image_id = int(raw.get("id"))
                    except (TypeError, ValueError):
                        continue
                    if image_id not in sent or image_id in used:
                        continue
                    used.add(image_id)
                    blocks.append({"kind": "figure", "id": image_id,
                                   "caption": self._clean(raw.get("caption")),
                                   "screen_text": self._clean(raw.get("screen_text")),
                                   "focus": _focus_box(raw.get("focus"))})
                elif kind == "bullets":
                    items = [self._clean(i) for i in raw.get("items") or []
                             if self._clean(i)]
                    if items:
                        blocks.append({"kind": "bullets", "items": items})
                else:
                    text = self._clean(raw.get("text"))
                    if text:
                        blocks.append({"kind": kind, "text": text})
            title = self._clean(raw_section.get("title"))
            if blocks:
                sections.append({"title": title,
                                 "summary": self._clean(raw_section.get("summary")),
                                 "blocks": blocks})

        omitted: List[int] = []
        for value in data.get("omit") or []:
            try:
                image_id = int(value)
            except (TypeError, ValueError):
                continue
            if image_id in sent and image_id not in used and image_id not in omitted:
                omitted.append(image_id)

        # لقطة أُرسلت ولم يذكرها النموذج لا في المستند ولا في المستبعد:
        # تعود إلى موضعها الزمني. الصمت عنها ليس قرارًا باستبعادها.
        forgotten = [k for i, k in sent.items() if i not in used and i not in omitted]
        if forgotten and sections:
            for keyframe in forgotten:
                self._insert_by_time(sections, keyframe, sent)
                used.add(keyframe.image_id)
            logger.info(f"أُعيدت {len(forgotten)} لقطة لم يذكرها النموذج إلى مواضعها.")
        if not sections:
            raise RewriteUnavailableError("ردّ المؤلّف المرئي بلا أقسام قابلة للاستعمال.")

        doc_type = str(data.get("doc_type") or "").strip().lower()
        return _Authored(
            doc_type=doc_type if doc_type in _DOC_TYPES else "",
            title=self._clean(data.get("title")),
            abstract=self._clean(data.get("abstract")),
            key_points=[self._clean(p) for p in data.get("key_points") or []
                        if self._clean(p)],
            sections=sections,
            omitted=omitted)

    @staticmethod
    def _insert_by_time(sections: List[dict], keyframe: KeyframeMetadata,
                        sent: Dict[int, KeyframeMetadata]) -> None:
        """يُدرج لقطة بعد آخر لقطة أقدم منها زمنًا، وإلا في آخر المستند."""
        best: Optional[Tuple[int, int]] = None
        for s_index, section in enumerate(sections):
            for b_index, block in enumerate(section["blocks"]):
                if block["kind"] == "figure" and sent[block["id"]].timestamp <= keyframe.timestamp:
                    best = (s_index, b_index)
        entry = {"kind": "figure", "id": keyframe.image_id, "caption": "",
                 "screen_text": "", "focus": None}
        if best is None:
            sections[0]["blocks"].insert(0, entry)
        else:
            sections[best[0]]["blocks"].insert(best[1] + 1, entry)

    # ------------------------------------------------------------------
    def _outline(self, authored: List[_Authored], fallback_title: str
                 ) -> Tuple[str, str, List[str]]:
        listing = "\n".join(f"- {s['title']}: {s['summary']}"
                            for a in authored for s in a.sections)
        try:
            raw = complete_with_retry(
                self.provider, OUTLINE_PROMPT,
                f"اسم الملف: {fallback_title}\nأقسام المستند:\n{listing}",
                max_tokens=800, timeout=self.config.timeout_seconds)
            data = _extract_json(raw) or {}
        except Exception as exc:
            logger.warning(f"تعذّر بناء عنوان المستند وملخّصه: {exc}")
            data = {}
        head = authored[0]
        return (self._clean(data.get("title")) or head.title,
                self._clean(data.get("abstract")) or head.abstract,
                [self._clean(p) for p in data.get("key_points") or [] if self._clean(p)]
                or head.key_points)

    @staticmethod
    def _assemble(authored: List[_Authored], keyframes: List[KeyframeMetadata],
                  segments: List[AudioSegment],
                  images_dir: Optional[Path] = None) -> DocumentPlan:
        by_id = {k.image_id: k for k in keyframes}
        sections: List[DocumentSection] = []
        omitted: List[int] = []
        figures_out = 0
        last_time = 0.0

        for part in authored:
            omitted.extend(part.omitted)
            for raw in part.sections:
                blocks: List[DocumentBlock] = []
                times = [by_id[b["id"]].timestamp for b in raw["blocks"]
                         if b["kind"] == "figure"]
                start = min(times) if times else last_time
                last_time = max([start] + times)
                for block in raw["blocks"]:
                    kind = block["kind"]
                    if kind == "figure":
                        keyframe = by_id[block["id"]]
                        figures_out += 1
                        filename = keyframe.filename
                        if block.get("focus") and images_dir is not None:
                            filename = _crop_focus(images_dir, keyframe.filename,
                                                   block["focus"]) or filename
                        blocks.append(DocumentBlock(
                            kind="figure", timestamp=keyframe.timestamp,
                            image_id=keyframe.image_id,
                            image_filename=filename,
                            caption=block["caption"],
                            # ما قرأه النموذج على الشاشة — يغذّي فحص الوفاء
                            # للمصدر وتمييز الصور حين يغيب OCR.
                            ocr_text=block["screen_text"] or keyframe.ocr_text))
                    elif kind == "bullets":
                        blocks.append(DocumentBlock(kind="bullets", bullets=block["items"]))
                    else:
                        blocks.append(DocumentBlock(kind=kind, text=block["text"]))
                sections.append(DocumentSection(
                    title=raw["title"] or f"القسم {len(sections) + 1}",
                    level=1, summary=raw["summary"], start_timestamp=start,
                    blocks=blocks))

        # نسبة كل مقطع تفريغ إلى قسم واحد بزمنه: تتبّع لا تكرار.
        starts = [s.start_timestamp or 0.0 for s in sections]
        buckets: List[List[int]] = [[] for _ in sections]
        for segment in segments:
            index = 0
            for position, start in enumerate(starts):
                if segment.start >= start:
                    index = position
            buckets[index].append(segment.id)
        for section, ids in zip(sections, buckets):
            if ids:
                target = next((b for b in section.blocks if b.image_id is None),
                              section.blocks[0])
                target.segment_ids = ids

        plan = DocumentPlan(title="", sections=sections)
        plan.total_segments_in = len(segments)
        plan.total_segments_out = sum(len(ids) for ids in buckets)
        plan.total_figures_in = len(keyframes)
        plan.total_figures_out = figures_out
        # لقطة لم تُرسَل أصلًا (تعذّر ترميزها) تُعدّ مستبعدة بإعلان.
        used = {b.image_id for s in sections for b in s.blocks if b.image_id is not None}
        for keyframe in keyframes:
            if keyframe.image_id not in used and keyframe.image_id not in omitted:
                omitted.append(keyframe.image_id)
        plan.figures_omitted = sorted(set(omitted))
        if omitted:
            logger.info(f"استُبعدت {len(set(omitted))} لقطة مكرّرة أو خارج الموضوع.")
        return plan
