"""تصدير ملفات الترجمة من عقد التفريغ.

لماذا هذه الوحدة رخيصة جدًا: التفريغ يعمل أصلًا بـ
``word_timestamps=True`` — وهو خيار يُبطئ Whisper بشكل ملموس — ويحفظ
لكل كلمة زمن بدايتها ونهايتها في ``transcription.json``. ثم لم تكن أي
وحدة تقرأ ``words`` إطلاقًا. أي أن البيانات مدفوعة الثمن وغير مستعملة.

ما تفتحه: مشاهدة المحاضرة بترجمة، رفعها على منصة تعليمية، والبحث داخل
الفيديو نفسه بدل البحث في المستند وحده.

الصيغتان:
    SRT — الأوسع دعمًا (VLC، YouTube، مشغّلات سطح المكتب)
    VTT — صيغة الويب (‏<track> في HTML5)
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Sequence, Tuple

from config.schemas import AudioSegment, TranscriptionResult

# أقصى طول للسطر الواحد في الترجمة. الأطول منه يُقسَم على سطرين.
MAX_LINE_CHARS = 42
# أقصى مدة لبطاقة واحدة — بطاقة طويلة تبقى على الشاشة بعد انتهاء الكلام
MAX_CUE_SECONDS = 7.0
MIN_CUE_SECONDS = 0.7


def _stamp(seconds: float, separator: str) -> str:
    """``HH:MM:SS,mmm`` لـ SRT و ``HH:MM:SS.mmm`` لـ VTT."""
    total_ms = max(0, int(round(float(seconds) * 1000)))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def _wrap(text: str, width: int = MAX_LINE_CHARS) -> str:
    """يقسم النص على سطرين كحد أقصى عند حدود الكلمات.

    الترجمة تُقرأ في ثانية أو اثنتين؛ سطر طويل يعبر الشاشة يجعلها بلا
    فائدة. القسمة عند الكلمات لا الحروف تحفظ التشكيل العربي سليمًا.
    """
    text = " ".join(text.split())
    if len(text) <= width:
        return text
    words = text.split(" ")
    first: List[str] = []
    length = 0
    for index, word in enumerate(words):
        if length + len(word) + (1 if first else 0) > width and first:
            return " ".join(first) + "\n" + " ".join(words[index:])
        first.append(word)
        length += len(word) + (1 if length else 0)
    return text


def _split_long_segment(segment: AudioSegment
                        ) -> List[Tuple[float, float, str]]:
    """يقسّم مقطعًا طويلًا إلى بطاقات، مستعينًا بتوقيت الكلمات إن وُجد.

    مقاطع Whisper قد تبلغ 20 ثانية. بطاقة ترجمة بهذا الطول تُظهر كلامًا
    قيل قبل عشر ثوانٍ. توقيت الكلمات — المحفوظ ولم يُستعمل — يسمح بقطع
    البطاقة عند حدود كلمة حقيقية بدل تقسيم زمني أعمى.
    """
    text = (segment.text_clean or segment.text_raw).strip()
    if not text:
        return []
    duration = max(0.0, segment.end - segment.start)
    if duration <= MAX_CUE_SECONDS:
        return [(segment.start, segment.end, text)]

    words = [w for w in (segment.words or []) if (w.word or "").strip()]
    if not words:
        # بلا توقيت كلمات: قسمة زمنية متساوية على عدد البطاقات المطلوب
        parts = max(2, int(duration // MAX_CUE_SECONDS) + 1)
        chunk = duration / parts
        tokens = text.split()
        per = max(1, len(tokens) // parts)
        cues = []
        for index in range(parts):
            piece = " ".join(tokens[index * per:(index + 1) * per]
                             if index < parts - 1 else tokens[index * per:])
            if piece:
                cues.append((segment.start + index * chunk,
                             segment.start + (index + 1) * chunk, piece))
        return cues

    cues: List[Tuple[float, float, str]] = []
    current: List[str] = []
    start = words[0].start
    for word in words:
        current.append(word.word.strip())
        spans_too_long = word.end - start >= MAX_CUE_SECONDS
        line_too_long = len(" ".join(current)) >= MAX_LINE_CHARS * 2
        if spans_too_long or line_too_long:
            cues.append((start, word.end, " ".join(current)))
            current, start = [], word.end
    if current:
        cues.append((start, words[-1].end, " ".join(current)))
    return cues


def build_cues(transcript: TranscriptionResult
               ) -> List[Tuple[float, float, str]]:
    """يحوّل المقاطع إلى بطاقات ترجمة نظيفة ومرتّبة بلا تداخل."""
    cues: List[Tuple[float, float, str]] = []
    for segment in transcript.segments:
        cues.extend(_split_long_segment(segment))

    cleaned: List[Tuple[float, float, str]] = []
    for start, end, text in cues:
        text = " ".join(text.split())
        if not text:
            continue
        end = max(end, start + MIN_CUE_SECONDS)
        # لا تتداخل بطاقة مع سابقتها: المشغّلات تُظهر الاثنتين معًا
        if cleaned and start < cleaned[-1][1]:
            previous = cleaned[-1]
            cleaned[-1] = (previous[0], min(previous[1], start), previous[2])
        cleaned.append((start, end, text))
    return [c for c in cleaned if c[1] > c[0]]


def render_srt(transcript: TranscriptionResult) -> str:
    blocks = []
    for index, (start, end, text) in enumerate(build_cues(transcript), start=1):
        blocks.append(
            f"{index}\n"
            f"{_stamp(start, ',')} --> {_stamp(end, ',')}\n"
            f"{_wrap(text)}\n")
    return "\n".join(blocks)


def render_vtt(transcript: TranscriptionResult) -> str:
    blocks = ["WEBVTT\n"]
    for start, end, text in build_cues(transcript):
        blocks.append(
            f"{_stamp(start, '.')} --> {_stamp(end, '.')}\n"
            f"{_wrap(text)}\n")
    return "\n".join(blocks)


def write_subtitles(transcript: TranscriptionResult, base_path: Path,
                    formats: Sequence[str] = ("srt", "vtt")) -> List[Path]:
    """يكتب ملفات الترجمة بجوار المستند ويعيد مساراتها.

    لا يُفشل المهمة عند الخطأ: الترجمة مخرج إضافي لا شرط نجاح.
    """
    renderers = {"srt": render_srt, "vtt": render_vtt}
    written: List[Path] = []
    for name in formats:
        render = renderers.get(name.lower())
        if render is None:
            continue
        content = render(transcript)
        if not content.strip() or content.strip() == "WEBVTT":
            continue
        path = base_path.with_suffix(f".{name.lower()}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written
