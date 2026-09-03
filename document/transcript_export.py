"""تصدير التفريغ نصًّا — دون بناء مستند Word.

ليس كل استخدام يحتاج وثيقة كاملة. من يريد النص لينسخه في رسالة أو
منشور، أو ليبحث فيه، ينتظر اليوم بناء غلاف وفهرس وصور بلا فائدة له.
هذا الوضع يقف عند التفريغ ويُخرج ما يُقرأ ويُنسخ مباشرةً.

الصيغ:
    ``.txt``  نص نظيف بفقرات مدموجة — للنسخ واللصق
    ``.md``   نفس النص بعناوين زمنية — للويكي والملاحظات
    ``.srt`` و ``.vtt``  عبر :mod:`document.subtitles`
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

from config.schemas import TranscriptionResult
from utils.timestamps import seconds_to_display

# طول الفقرة المدموجة بالحروف — نفس منطق المخطِّط: مقاطع Whisper قصيرة
# (‏3-10 ثوانٍ) وعرضها مفردة يُنتج جدارًا مفتّتًا.
PARAGRAPH_CHARS = 700
# فجوة صمت تُعدّ حدًّا طبيعيًا لفقرة جديدة
PARAGRAPH_GAP_SECONDS = 2.5


def _paragraphs(transcript: TranscriptionResult
                ) -> List[tuple[float, str]]:
    """يدمج المقاطع في فقرات مقروءة ويعيد (زمن البداية، النص)."""
    paragraphs: List[tuple[float, str]] = []
    buffer: List[str] = []
    start: Optional[float] = None
    previous_end: Optional[float] = None

    for segment in transcript.segments:
        text = (segment.text_clean or segment.text_raw).strip()
        if not text:
            continue
        gap = (segment.start - previous_end) if previous_end is not None else 0.0
        too_long = sum(len(part) for part in buffer) >= PARAGRAPH_CHARS
        if buffer and (too_long or gap >= PARAGRAPH_GAP_SECONDS):
            paragraphs.append((start or 0.0, " ".join(buffer)))
            buffer, start = [], None
        if start is None:
            start = segment.start
        buffer.append(text)
        previous_end = segment.end

    if buffer:
        paragraphs.append((start or 0.0, " ".join(buffer)))
    return paragraphs


def render_text(transcript: TranscriptionResult,
                with_timecodes: bool = False) -> str:
    """نص نظيف. التوقيتات اختيارية — أغلب من ينسخ النص لا يريدها."""
    lines: List[str] = []
    for start, text in _paragraphs(transcript):
        if with_timecodes:
            lines.append(f"[{seconds_to_display(start)}] {text}")
        else:
            lines.append(text)
    return "\n\n".join(lines) + ("\n" if lines else "")


def render_markdown(transcript: TranscriptionResult, title: str) -> str:
    """‏Markdown بعناوين زمنية — صالح للويكي وتطبيقات الملاحظات."""
    lines = [f"# {title}", ""]
    engine = transcript.engine or {}
    if engine.get("model"):
        lines += [f"> تفريغ آلي · {engine.get('name', '')} "
                  f"{engine.get('model', '')}".strip(), ""]
    for start, text in _paragraphs(transcript):
        lines += [f"## {seconds_to_display(start)}", "", text, ""]
    return "\n".join(lines)


def write_transcript(transcript: TranscriptionResult, base_path: Path,
                     title: str,
                     formats: Sequence[str] = ("txt", "md", "srt", "vtt"),
                     with_timecodes: bool = False) -> List[Path]:
    """يكتب صيغ التفريغ المطلوبة بجوار بعضها ويعيد المسارات المكتوبة."""
    from document.subtitles import render_srt, render_vtt

    if not transcript.segments:
        return []

    renderers = {
        "txt": lambda: render_text(transcript, with_timecodes),
        "md": lambda: render_markdown(transcript, title),
        "srt": lambda: render_srt(transcript),
        "vtt": lambda: render_vtt(transcript),
    }
    written: List[Path] = []
    base_path.parent.mkdir(parents=True, exist_ok=True)
    for name in formats:
        render = renderers.get(name.lower())
        if render is None:
            continue
        content = render()
        if not content.strip() or content.strip() == "WEBVTT":
            continue
        path = base_path.with_suffix(f".{name.lower()}")
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written
