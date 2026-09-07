"""التفريغ الصوتي المحلي.

التصحيحان الجوهريان مقابل الحزمة الأصلية:

1. **الـ fallback يغطي التنفيذ لا التحميل فقط.** ``try/except`` الأصلي
   يلفّ ``_load_model()`` وحده، بينما أخطاء CUDA OOM و cuDNN تقع غالبًا
   أثناء ``.transcribe()``. المواصفة نفسها تنص على "فشل التحميل
   **/التنفيذ**" — والكود كان ينفّذ نصفها.

2. **إعدادات عربية حاسمة**: ``condition_on_previous_text=False`` يمنع
   حلقات التكرار الهلوسي على فترات الصمت، وهي أشهر أعطال Whisper مع
   العربية. ولأن ``segments`` مولّد كسول (generator)، فإن الاستهلاك
   داخل try هو ما يجعل الالتقاط ممكنًا أصلًا.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, List, Optional

from faster_whisper import WhisperModel

from audio.text_cleaner import (
    MIN_LOOP_SEGMENTS,
    clean_segment_text,
    find_repeated_runs,
)
from config.schemas import (AudioSegment, TranscriptionCheckpoint,
                            TranscriptionResult, WordTimestamp)
from config.settings import TranscriptionConfig
from core.exceptions import ResourceAllocationError
from utils.cancellation import CancellationToken
from utils.gpu_manager import GPUManager
from utils.logger import logger
from utils.timestamps import estimate_remaining, humanize_duration

#: أقصى طول لمقطع تفريغ بالثواني قبل أن يُقسَّم على حدود الكلمات.
#:
#: سبب وجوده: التفريغ المُجمَّع أسرع كثيرًا، لكنه يُنتج مقاطع أطول
#: بكثير — قياس على 132 ثانية من كلام حقيقي: وسيط 29.8ث وأقصى 32.9ث،
#: مقابل 9.1ث و10.3ث في المسار المتسلسل. وذلك انحدار حقيقي في مكانين:
#:
#:   * **ملفات الترجمة** تُبنى من حدود المقاطع مباشرةً. سطر ترجمة مدّته
#:     ثلاثون ثانية لا يُقرأ ولا يُتابَع.
#:   * **تقسيم الفقرات** في ``document/planner`` يعتمد الحدود نفسها،
#:     فتصير الفقرة أخشن ثلاث مرات.
#:
#: القيمة تقارب وسيط المسار المتسلسل، فلا يشعر المستخدم بفرق في المخرج
#: بين المسارين — وهو شرط قبول التسريع أصلًا.
MAX_SEGMENT_SECONDS = 12.0

#: لا نُنتج شظايا: مقطع أقصر من هذا لا يُقسَّم ولا يَنتج عن قسمة.
MIN_SEGMENT_SECONDS = 2.5

#: فجوة صمت داخل المقطع تستوجب قسمته مهما كانت مدّته الكلية.
#:
#: وُجد هذا على مادّة حقيقية: مقطع «ثم ندخل خاصة. أولا بيدخل معنا
#: الموقع.» يمتدّ 16 ثانية على سبع كلمات، وبينها فجوة صامتة **12.6
#: ثانية**. حدُّ المدّة وحده لا يكفي: مقطع مدّته تسع ثوانٍ بداخله فجوة
#: أربع ثوانٍ يمرّ منه، ويخرج سطر ترجمة معلّق على الشاشة طوال الصمت،
#: وفقرةً في المستند تجمع كلامًا لا صلة بين طرفيه.
#:
#: القيمة محافظة: الوقفة الطبيعية بين جملتين في المحاضرة أقصر من
#: ثانيتين، فلا نقسم كلامًا متّصلًا.
MAX_INTERNAL_GAP_SECONDS = 2.5

#: حدّ ``hotwords`` في faster-whisper هو ``max_length // 2 - 1`` = 223
#: رمزًا. العربية تُرمَّز بكثافة أعلى من اللاتينية، فنبقى دون الحدّ
#: بهامش واسع بالحروف بدل محاولة عدّ الرموز خارج المُرمِّز.
_HOTWORDS_CHAR_LIMIT = 400

#: كل كم ثانية من زمن **المعالجة** تُحفظ نقطة استئناف.
#:
#: الحدّ على زمن المعالجة لا على زمن الصوت، لأن ما يهمّ المستخدم هو ما
#: يخسره الانقطاع بالدقائق — لا كم ثانية صوت فُرّغت. وستّون ثانية تعني
#: أن أسوأ خسارة دقيقةٌ واحدة من أربع ساعات ونصف.
#:
#: والكتابة رخيصة أمام ذلك: مقاطع محاضرة ثلاث ساعات نحو 3–5 ميغابايت
#: من JSON، تُكتب كتابةً ذرّية كل دقيقة. أي أقل من واحد بالألف من زمن
#: المعالجة على القرص.
CHECKPOINT_SECONDS = 60.0

# أنماط أخطاء تستدعي السقوط إلى CPU
_GPU_ERROR_MARKERS = (
    "cuda", "cublas", "cudnn", "out of memory", "device", "gpu", "nvrtc",
)


def _is_gpu_error(exc: BaseException) -> bool:
    message = f"{type(exc).__name__} {exc}".lower()
    return any(marker in message for marker in _GPU_ERROR_MARKERS)


def collapse_repeated_segments(segments: List[AudioSegment]
                               ) -> List[AudioSegment]:
    """يطوي حلقة هلوسية ممتدّة عبر مقاطع متتالية إلى مقطع واحد.

    ‏Whisper على الصمت أو الموسيقى لا يُكرّر داخل المقطع بل **يُنتج
    مقاطع**: ثمانية مقاطع نصّ كلٍّ منها «شكرًا لكم». الطيّ داخل المقطع
    لا يراها إطلاقًا، فتصل صفحةً كاملة إلى المستند.

    **النصّ الخام لا يُمسّ** (ADR-005): المقطع الناتج يحمل تجميع كل
    النصوص الخام، والمطويّ هو ``text_clean`` وحده — أي أن ما قاله
    المتحدّث محفوظ في ``transcription.json`` كما سُمع، والمستند وحده هو
    ما ينظّف.

    توقيت الكلمات يبقى كاملًا: تُستعمل في وضع الصور، فحذفها يُزيح
    اللقطات عن مواضعها.
    """
    if len(segments) < MIN_LOOP_SEGMENTS:
        return segments

    runs = dict(find_repeated_runs([s.text_clean for s in segments]))
    if not runs:
        return segments

    result: List[AudioSegment] = []
    index = 0
    while index < len(segments):
        end = runs.get(index)
        if end is None:
            result.append(segments[index])
            index += 1
            continue

        run = segments[index:end]
        logger.info("طُويت حلقة تكرار: %d مقاطع ← 1 عند %.1f ث (%r)",
                    len(run), run[0].start, run[0].text_clean[:40])
        result.append(AudioSegment(
            id=run[0].id, start=run[0].start, end=run[-1].end,
            # الخام كاملًا — ADR-005
            text_raw=" ".join(s.text_raw for s in run if s.text_raw),
            text_clean=run[0].text_clean,
            words=[w for s in run for w in s.words],
        ))
        index = end

    for number, segment in enumerate(result, start=1):
        segment.id = number
    return result


def _widest_gap(words: List[WordTimestamp]) -> Optional[int]:
    """موضع أوسع فجوة صمت تتجاوز الحدّ، أو ``None``.

    مستقلّ عن ``_split_point`` عمدًا: هذا يقسم على **عطل** (صمت طويل
    داخل مقطع)، وذاك يقسم على **طول** (مقطع أطول من سطر ترجمة مقروء).
    الأول لا يحترم ``MIN_SEGMENT_SECONDS`` لأن الشظية أهون من سطر
    معلّق على الشاشة اثنتي عشرة ثانية.
    """
    widest, index = MAX_INTERNAL_GAP_SECONDS, None
    for position in range(1, len(words)):
        gap = words[position].start - words[position - 1].end
        if gap > widest:
            widest, index = gap, position
    return index


def _split_point(words: List[WordTimestamp]) -> Optional[int]:
    """أفضل موضع لقسمة مقطع، أو ``None`` إن لم يوجد موضع صالح.

    نفضّل الصمت: أكبر فجوة بين كلمتين هي على الأرجح نهاية جملة أو
    وقفة المحاضر. والكلمة المنتهية بعلامة ترقيم تُرجَّح، لأن القسمة
    عندها تُنتج سطر ترجمة وفقرةً يبدآن من أول الكلام لا من وسطه.
    """
    span_start, span_end = words[0].start, words[-1].end
    midpoint = (span_start + span_end) / 2.0
    half = max(1e-6, (span_end - span_start) / 2.0)

    best_index: Optional[int] = None
    best_score = -1.0
    for index in range(1, len(words)):
        previous, current = words[index - 1], words[index]
        # لا نقسم قسمةً تُنتج شظية على أيّ من الطرفين
        if (previous.end - span_start < MIN_SEGMENT_SECONDS
                or span_end - current.start < MIN_SEGMENT_SECONDS):
            continue

        gap = max(0.0, current.start - previous.end)
        # علامة الترقيم تساوي نصف ثانية صمت في الترجيح — تكفي لتفضيل
        # نهاية جملة على فجوة أطول قليلًا في منتصفها.
        sentence = 0.5 if previous.word.rstrip()[-1:] in ".!?؟،؛:…" else 0.0
        # ترجيح القرب من المنتصف. بدونه يفوز آخر موضع صالح دائمًا حين
        # تتساوى الفجوات — لأن خطأ الفاصلة العائمة يتراكم فيجعل الفجوة
        # الأخيرة أكبر بمقدار لا معنى له — فتخرج قسمة مائلة تمامًا
        # (32.8ث و6.8ث) بدل نصفين متوازنين.
        centrality = 0.25 * (1.0 - abs(current.start - midpoint) / half)

        score = gap + sentence + centrality
        if score > best_score + 1e-9:
            best_score, best_index = score, index
    return best_index


def split_long_segments(segments: List[AudioSegment],
                        max_seconds: float = MAX_SEGMENT_SECONDS
                        ) -> List[AudioSegment]:
    """يقسّم المقاطع الأطول من الحدّ على حدود الكلمات، ويعيد الترقيم.

    يحافظ على النصّ كاملًا: القسمة تعيد توزيع الكلمات نفسها، ولا تحذف
    ولا تضيف حرفًا — ``assert_lossless`` (ADR-010) يعتمد على ذلك.

    مقطع بلا توقيت كلمات يُترك كما هو: لا معلومات تكفي لقسمته بأمان.
    """
    result: List[AudioSegment] = []
    queue = list(segments)
    while queue:
        segment = queue.pop(0)
        if len(segment.words) < 2:
            result.append(segment)
            continue

        # فجوة صمت كبيرة تستوجب القسمة مهما كانت المدّة: مقطع من تسع
        # ثوانٍ بداخله صمت أربع ثوانٍ يمرّ من حدّ المدّة، ويُخرج سطر
        # ترجمة معلّقًا على الشاشة طوال الصمت.
        gap_index = _widest_gap(segment.words)
        if segment.end - segment.start <= max_seconds and gap_index is None:
            result.append(segment)
            continue

        index = gap_index if gap_index is not None else _split_point(segment.words)
        if index is None:
            result.append(segment)
            continue

        left_words, right_words = segment.words[:index], segment.words[index:]
        left_text = " ".join(w.word for w in left_words).strip()
        right_text = " ".join(w.word for w in right_words).strip()
        # **كلا** الجزأين يعود إلى الطابور. إعادة الأيمن وحده كانت تترك
        # جزءًا أيسر أطول من الحدّ بلا فحص ثانٍ، فتخرج قسمة واحدة عقيمة
        # (32.8ث و6.8ث) بدل قسمة كاملة. القسمة تُنقص عدد الكلمات دائمًا،
        # فالتكرار ينتهي حتمًا.
        queue[:0] = [
            AudioSegment(id=segment.id, start=segment.start,
                         end=left_words[-1].end, text_raw=left_text,
                         text_clean=clean_segment_text(left_text),
                         words=left_words),
            AudioSegment(id=segment.id, start=right_words[0].start,
                         end=segment.end, text_raw=right_text,
                         text_clean=clean_segment_text(right_text),
                         words=right_words),
        ]

    for number, segment in enumerate(result, start=1):
        segment.id = number
    return result


class TranscriptionEngine:
    def __init__(self, config: Optional[TranscriptionConfig] = None) -> None:
        self.config = config or TranscriptionConfig()
        # الإنتاج يُسقط النموذج بعد كل مهمة لتحرير VRAM. أدوات القياس
        # الدفعية تضبط هذه الراية لتحميله مرة واحدة.
        self.keep_model_loaded = False
        # حاجز ثانٍ ضد التنزيل الصامت (ADR-014): حتى لو أخطأ فحص التوفّر،
        # ``local_files_only`` يمنع faster-whisper من الاتصال بالشبكة.
        # يرفعه الـ pipeline فقط بعد إذن صريح من المستخدم.
        self.allow_download = False
        self._cached: Optional[tuple[str, str, WhisperModel]] = None
        if self.config.download_root is None:
            from audio.model_manager import default_cache_root
            self.config = self.config.model_copy(
                update={"download_root": default_cache_root()})

    def _effective_prompt(self) -> Optional[str]:
        """موجّه البداية — أسلوب الكتابة فقط، بلا مصطلحات.

        المصطلحات كانت تُدمج هنا، وكان ذلك **عطلًا لا ميزة ضعيفة**:
        ‏faster-whisper يقرأ ``initial_prompt`` من ``previous_tokens``،
        ثم يُصفّر ``prompt_reset_since`` بعد **كل** نافذة لأن إعدادنا
        ``condition_on_previous_text=False``. فالقاموس يصل نافذة واحدة
        (~30 ثانية) ثم يُهمَل إلى الأبد: 0.3٪ من محاضرة ثلاث ساعات.

        المصطلحات انتقلت إلى ``hotwords`` — انظر ``_effective_hotwords``.
        """
        base = (self.config.initial_prompt or "").strip()
        return base or None

    def _effective_hotwords(self) -> Optional[str]:
        """المصطلحات كـ hotwords — تُحقَن في موجّه **كل** نافذة.

        ‏``get_prompt`` في faster-whisper يُدرج ``hotwords`` بلا شرط،
        مستقلةً تمامًا عن ``previous_tokens`` وعن
        ``condition_on_previous_text``. هذا هو المعامل الذي كان يجب
        استعماله منذ البداية.

        الحدّ في المكتبة ``max_length // 2 - 1`` = 223 رمزًا. نقصّ على
        حدود الكلمات لا على حرف عشوائي: قصّ مصطلح في منتصفه يُدخل
        الضجيج بدل أن يمنعه.
        """
        glossary = " ".join((self.config.glossary or "").split())
        if not glossary:
            return None
        if len(glossary) <= _HOTWORDS_CHAR_LIMIT:
            return glossary
        kept: List[str] = []
        length = 0
        for term in glossary.split(" "):
            extra = len(term) + (1 if kept else 0)
            if length + extra > _HOTWORDS_CHAR_LIMIT:
                break
            kept.append(term)
            length += extra
        return " ".join(kept) or None

    def _resolve_threads(self) -> int:
        """عدد الخيوط: الإعداد إن حُدّد، وإلا أنوية المعالج الفعلية.

        الرقم الثابت لا يناسب جهازين مختلفين: أربعة خيوط على معالج
        بثمانية أنوية تترك نصف الأداء، وعلى نواتين تُثقِله بتبديل سياق.
        نترك نواةً للنظام والواجهة حتى لا يتجمّد الجهاز أثناء ساعات
        التفريغ.
        """
        configured = int(self.config.cpu_threads or 0)
        if configured > 0:
            return configured
        import os

        cores = os.cpu_count() or 4
        return max(1, cores - 1) if cores > 2 else cores

    def _vad_options(self) -> Optional[dict]:
        """معاملات VAD الصريحة، أو ``None`` حين يكون VAD معطّلًا.

        كان VAD يعمل بقيم المكتبة الافتراضية بلا وسيلة لضبطه. القيم
        الافتراضية هنا مطابقة لها تمامًا، فالترقية لا تُغيّر سلوكًا —
        لكنها تفتح الباب للقياس والضبط على مادّة حقيقية.
        """
        if not self.config.vad_filter:
            return None
        cfg = self.config
        options = {
            "threshold": cfg.vad_threshold,
            "min_speech_duration_ms": cfg.vad_min_speech_duration_ms,
            "min_silence_duration_ms": cfg.vad_min_silence_duration_ms,
            "speech_pad_ms": cfg.vad_speech_pad_ms,
        }
        # صفر يعني «بلا حدّ» — وهو افتراضي المكتبة (``inf``). لا نمرّر
        # ``inf`` من الإعداد لأن YAML لا يحمله بشكل محمول.
        if cfg.vad_max_speech_duration_s > 0:
            options["max_speech_duration_s"] = cfg.vad_max_speech_duration_s
        return options

    def _resolve_batch_size(self, device: str) -> int:
        """حجم الدفعة الفعلي. ``1`` يعني المسار المتسلسل — وهو الافتراضي.

        ‏CTranslate2 يستطيع فكّ عدّة نوافذ معًا. أعطى ذلك 2.3–6.6× على
        كلام **إنجليزي** بنموذج ``tiny``؛ وعلى المادّة الحقيقية — شرح
        عربي بنموذج الإنتاج ``large-v3-turbo`` — لم يُعطِ مكسبًا
        إطلاقًا (182.7 ث متسلسلًا مقابل 217.7 ث مُجمَّعًا، والتشتّت على
        هذه الآلة أوسع من الفرق).

        فالافتراضي متسلسل: تغييرٌ يُبدّل حدود المقاطع والنصّ بلا مكسب
        مُثبَت لا يصلح افتراضيًا. ``batch_size: 8`` متاح لمن يقيسه على
        عتاده — وعلى بطاقة رسومية قد تختلف الصورة، ولم يُقَس ذلك بعد.

        الدفعة تشترط VAD: المسار المُجمَّع يبني دفعاته من مقاطع الكلام
        التي يكشفها. لو عُطّل VAD نسقط إلى المتسلسل بدل أن ننهار.
        """
        configured = int(getattr(self.config, "batch_size", 0) or 0)
        if configured == 1:
            return 1
        if not self.config.vad_filter:
            if configured > 1:
                logger.warning("التفريغ المُجمَّع يحتاج VAD — العودة إلى المتسلسل.")
            return 1
        if configured > 1:
            return configured
        # تلقائي: الذاكرة على المعالج أضيق منها على البطاقة.
        return 16 if device == "cuda" else 8

    @staticmethod
    def _batched(model: WhisperModel):
        from faster_whisper import BatchedInferencePipeline

        return BatchedInferencePipeline(model=model)

    def _load_model(self, device: str, compute_type: str) -> WhisperModel:
        if (self.keep_model_loaded and self._cached
                and self._cached[0] == device and self._cached[1] == compute_type):
            return self._cached[2]
        threads = self._resolve_threads()
        logger.info(f"تحميل Whisper ({self.config.model_size}) على "
                    f"{device}/{compute_type} · خيوط={threads} · "
                    f"beam={self.config.beam_size}")

        # تشخيص صريح لأخطر سبب بطء غير مرئي: بيئتا OpenMP في عملية واحدة.
        # بلا هذا السطر يظهر العطل كـ«التفريغ بطيء بلا سبب» ولا شيء
        # يدلّ عليه في السجلّ.
        import sys as _sys

        if "torch" in _sys.modules:
            logger.warning(
                "‏PyTorch محمَّل في هذه العملية مع CTranslate2 — بيئتا "
                "OpenMP تتنازعان الأنوية وقد يتضاعف زمن التفريغ. "
                "لا تُفعّل المحرّكات الاختيارية إلا عند استعمالها فعلًا.")
        model = WhisperModel(
            self.config.model_size,
            device=device,
            compute_type=compute_type,
            cpu_threads=self._resolve_threads(),
            download_root=str(self.config.download_root)
            if self.config.download_root else None,
            local_files_only=not self.allow_download,
        )
        if self.keep_model_loaded:
            self._cached = (device, compute_type, model)
        return model

    def transcribe(
        self,
        audio_path: Path,
        cancel_token: Optional[CancellationToken] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        checkpoint: Optional[Callable[[List[AudioSegment],
                                       List[WordTimestamp], float],
                                      None]] = None,
        resume: Optional[TranscriptionCheckpoint] = None,
    ) -> TranscriptionResult:
        """``checkpoint`` يُستدعى دوريًّا بما اكتمل حتى الآن.

        ``resume`` مقاطعُ تشغيلٍ سابق انقطع؛ حين يُمرَّر يكون
        ``audio_path`` هو **بقيّة** الصوت وحدها، وتُزاح توقيتات المقاطع
        الجديدة بـ ``resume.resume_at`` لتبقى بزمن الصوت الكامل.
        """
        device, compute_type = GPUManager.resolve_device_config(
            self.config.device, self.config.compute_type)
        fallback_used = False

        try:
            return self._run(audio_path, device, compute_type,
                             cancel_token, progress_callback, fallback_used,
                             checkpoint, resume)
        except Exception as exc:
            # الإلغاء ليس خطأ عتاد — يُمرَّر كما هو
            from core.exceptions import PipelineCancelledError
            if isinstance(exc, PipelineCancelledError):
                raise
            if device != "cuda" or not _is_gpu_error(exc):
                raise ResourceAllocationError(f"فشل التفريغ الصوتي: {exc}") from exc

            logger.warning(f"فشل على CUDA أثناء التحميل أو التنفيذ ({exc}); السقوط إلى CPU.")
            GPUManager.release_memory()
            device, compute_type = GPUManager.resolve_device_config("cpu")
            fallback_used = True
            if progress_callback:
                progress_callback(0.0, "تعذّر استخدام كرت الشاشة — التحويل إلى المعالج.")
            return self._run(audio_path, device, compute_type,
                             cancel_token, progress_callback, fallback_used,
                             checkpoint, resume)

    # ------------------------------------------------------------------
    def _run(
        self,
        audio_path: Path,
        device: str,
        compute_type: str,
        cancel_token: Optional[CancellationToken],
        progress_callback: Optional[Callable[[float, str], None]],
        fallback_used: bool,
        checkpoint: Optional[Callable] = None,
        resume: Optional[TranscriptionCheckpoint] = None,
    ) -> TranscriptionResult:
        model: Optional[WhisperModel] = None
        try:
            model = self._load_model(device, compute_type)
            cfg = self.config
            options = dict(
                language=cfg.language,
                vad_filter=cfg.vad_filter,
                vad_parameters=self._vad_options(),
                word_timestamps=cfg.word_timestamps,
                beam_size=cfg.beam_size,
                # حاسم للعربية: يمنع تسرّب السياق وحلقات التكرار
                condition_on_previous_text=cfg.condition_on_previous_text,
                no_speech_threshold=cfg.no_speech_threshold,
                compression_ratio_threshold=cfg.compression_ratio_threshold,
                initial_prompt=self._effective_prompt(),
                # المصطلحات تصل كل نافذة، لا أول ثلاثين ثانية فقط
                hotwords=self._effective_hotwords(),
                # يمنع اختلاق نصّ فوق الصمت — أشهر عطل عربي: «شكرًا لكم»
                # تتكرّر صفحةً كاملة. شرطه (توقيت الكلمات) مُفعَّل أصلًا.
                hallucination_silence_threshold=cfg.hallucination_silence_threshold,
            )
            batch_size = self._resolve_batch_size(device)
            if batch_size > 1:
                transcriber = self._batched(model)
                segments_iter, info = transcriber.transcribe(
                    str(audio_path), batch_size=batch_size, **options)
            else:
                segments_iter, info = model.transcribe(str(audio_path), **options)

            # ``offset`` زمنُ ما فُرّغ في تشغيلٍ سابق. الصوت المُمرَّر
            # هنا بقيّةٌ تبدأ من الصفر، فتوقيتاتها تُزاح لتبقى بزمن
            # الصوت الكامل — وإلا حملت الصورُ والترجمة توقيتًا خاطئًا.
            offset = resume.resume_at if resume else 0.0
            remaining_total = float(getattr(info, "duration", 0.0) or 0.0)
            # المقام هو الصوت **الكامل**: بعد استئنافٍ عند الساعة الرابعة
            # يجب أن يبدأ الشريط من 80٪ لا من الصفر.
            total = ((resume.audio_seconds if resume else 0.0)
                     or (remaining_total + offset))
            started_at = time.monotonic()
            if remaining_total > 0:
                if offset > 0:
                    logger.info(
                        f"استئناف التفريغ من {humanize_duration(offset)} — "
                        f"بقي {humanize_duration(remaining_total)}")
                else:
                    logger.info(
                        f"مدة الصوت {humanize_duration(total)} — بدء التفريغ")
            segments: List[AudioSegment] = list(resume.segments) if resume else []
            words: List[WordTimestamp] = list(resume.words) if resume else []
            raw_parts: List[str] = []
            clean_parts: List[str] = []
            last_emit = -1.0
            last_checkpoint = time.monotonic()

            # استهلاك المولّد داخل try — هنا تقع أخطاء CUDA فعليًا
            for index, seg in enumerate(segments_iter, start=len(segments) + 1):
                if cancel_token:
                    cancel_token.raise_if_cancelled()
                    cancel_token.wait_if_paused()

                raw = seg.text.strip()
                clean = clean_segment_text(raw)
                raw_parts.append(raw)
                if clean:
                    clean_parts.append(clean)

                seg_words = [
                    WordTimestamp(word=w.word.strip(),
                                  start=w.start + offset, end=w.end + offset,
                                  probability=getattr(w, "probability", None))
                    for w in (seg.words or [])
                ]
                words.extend(seg_words)
                segments.append(AudioSegment(
                    id=index, start=seg.start + offset, end=seg.end + offset,
                    text_raw=raw, text_clean=clean, words=seg_words,
                ))

                # نقطة الحفظ: كل ``CHECKPOINT_SECONDS`` من زمن **المعالجة**
                # لا من زمن الصوت. الحدّ على الأول لأن المطلوب تحديد ما
                # يُفقد بالانقطاع، وهو يُقاس بالدقائق الضائعة لا بالثواني
                # المُفرَّغة. وتُمرَّر المقاطع الخام قبل إعادة التشكيل.
                now = time.monotonic()
                if checkpoint and now - last_checkpoint >= CHECKPOINT_SECONDS:
                    last_checkpoint = now
                    checkpoint(list(segments), list(words),
                               segments[-1].end)

                # خنق التحديثات: مرة كل ثانيتين من زمن الفيديو، لا كل مقطع
                if (progress_callback and total > 0
                        and seg.end + offset - last_emit >= 2.0):
                    last_emit = seg.end + offset
                    fraction = min(1.0, (seg.end + offset) / total)
                    # الوقت المتبقي: بدونه تبدو النسبة الزاحفة وكأن
                    # البرنامج متجمد، والمستخدم يلغي عملًا سليمًا
                    remaining = estimate_remaining(
                        time.monotonic() - started_at, fraction)
                    eta = (f" · متبقٍ ~{humanize_duration(remaining)}"
                           if remaining is not None else "")
                    progress_callback(
                        fraction,
                        f"التفريغ {fraction * 100:.1f}% "
                        f"({(seg.end + offset) / 60:.0f} من "
                        f"{total / 60:.0f} دقيقة){eta}")

            # ترتيب مقصود: الطيّ أولًا ثم القسمة. حلقة التكرار تُطوى
            # إلى مقطع واحد قد يكون طويلًا، فتقسمه الخطوة التالية.
            before = len(segments)
            segments = collapse_repeated_segments(segments)
            segments = split_long_segments(segments)
            if len(segments) != before:
                logger.info("إعادة تشكيل المقاطع: %d ← %d", before, len(segments))
            # يُعاد بناء النصّ المجمّع دائمًا: الطيّ يغيّر ``text_clean``
            # حتى حين لا يتغيّر عدد المقاطع.
            raw_parts = [s.text_raw for s in segments if s.text_raw]
            clean_parts = [s.text_clean for s in segments if s.text_clean]

            return TranscriptionResult(
                language=getattr(info, "language", self.config.language),
                full_text_raw=" ".join(raw_parts),
                full_text_clean=" ".join(clean_parts),
                segments=segments,
                words=words,
                engine={
                    "name": "faster-whisper",
                    "model": self.config.model_size,
                    "device": device,
                    "compute_type": compute_type,
                    "fallback_used": fallback_used,
                },
            )
        finally:
            if not self.keep_model_loaded:
                del model
                GPUManager.release_memory()
