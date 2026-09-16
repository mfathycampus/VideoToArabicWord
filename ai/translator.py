"""ترجمة بطاقات الترجمة — والتوقيتات لا تُمسّ.

**القرار المركزي: لا يُسأل النموذج عن التوقيت إطلاقًا** — مطابقٌ لما في
``ai/study_builder``. البطاقات تُرقَّم، ويُطلب منه نصٌّ مترجم **لكل
رقم**، ثم تُركَّب الترجمة على التوقيتات الأصلية كما هي. نموذجٌ يُعيد
كتابة ``00:12:40 --> 00:12:44`` سيخترع أرقامًا في مكانٍ ما من ملفٍّ فيه
ألف بطاقة، ولن يظهر الخطأ إلا عند الطالب الذي يشاهد.

**وعدد البطاقات ثابت لا يتغيّر.** النموذج يميل إلى دمج بطاقتين
قصيرتين في جملة أفصح — وهو تحسينٌ لغويّ يُزيح الملفّ كلّه بعدها. فإن
عاد بعددٍ مخالف، تُرفض الدفعة وتبقى بطاقاتها **بنصّها الأصلي** بدل
إفساد الملفّ كلّه.

**وترجمة الترجمات لا ترجمة المستند.** الملفّ الذي يحتاجه موظّف لا
يتكلّم العربية هو ملفّ ترجمة يقرؤه وهو يشاهد؛ وترجمة المستند كاملًا
تعني إعادة بناء كل تنسيق RTL في اتجاه آخر، وهي ميزةٌ أخرى بحجم آخر.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ai.providers import LLMProvider, RewriteUnavailableError, complete_with_retry
from config.schemas import TranscriptionResult
from document.subtitles import _stamp, _wrap, build_cues
from utils.logger import logger

#: ``الرمز: (الاسم العربي، الاسم بلغته)`` — الاسم بلغته يدخل المُوجِّه،
#: فالنموذج يفهم "English" أوثق مما يفهم "الإنجليزية".
LANGUAGES = {
    "en": ("الإنجليزية", "English"),
    "fr": ("الفرنسية", "French"),
    "es": ("الإسبانية", "Spanish"),
    "de": ("الألمانية", "German"),
    "tr": ("التركية", "Turkish"),
    "ur": ("الأردية", "Urdu"),
    "id": ("الإندونيسية", "Indonesian"),
    "hi": ("الهندية", "Hindi"),
}

#: لغات تُكتب من اليمين إلى اليسار — تُكتب لها ``<track>`` باتجاهها.
RTL_LANGUAGES = {"ar", "ur", "fa", "he"}

SYSTEM_PROMPT = """أنت مترجم محترف. أمامك بطاقات ترجمة مرقّمة من محاضرة عربية.

القواعد الملزمة:
1. ترجم كل بطاقة إلى {language} ترجمةً طبيعية لا حرفية.
2. أعِد **العدد نفسه** من البطاقات بالأرقام نفسها. لا تدمج بطاقتين
   ولا تقسم بطاقة مهما بدا ذلك أفصح — الأرقام مرتبطة بتوقيتات ثابتة.
3. البطاقة تُقرأ في ثانيتين: اجعلها موجزة بقدر الأصل.
4. المصطلحات الأجنبية التي وردت بحروف لاتينية تبقى كما هي.
5. لا تضف تعليقًا ولا شرحًا ولا ترقيمًا زائدًا.

أعِد كل بطاقة في سطر واحد بالشكل:  رقم|النصّ المترجم
ولا شيء غير ذلك."""

_LINE = re.compile(r"^\s*(\d+)\s*\|\s*(.+?)\s*$")


@dataclass
class TranslationConfig:
    target: str = "en"
    #: بطاقات الدفعة الواحدة. ستّون بطاقة ≈ ثلاث دقائق كلام — سياق كافٍ
    #: للضمائر والإحالة، وأصغر من أن يفقد النموذج فيه ترقيمه.
    batch_size: int = 60
    timeout_seconds: int = 180


def _render_batch(cues: Sequence[Tuple[int, float, float, str]]) -> str:
    return "\n".join(f"{index}|{text}" for index, _s, _e, text in cues)


def _parse(raw: str, expected: Sequence[int]) -> Optional[dict]:
    """يقرأ ``رقم|نصّ``. يرفض الدفعة كلّها إن اختلّ الترقيم."""
    found: dict = {}
    for line in (raw or "").splitlines():
        match = _LINE.match(line)
        if match:
            found[int(match.group(1))] = match.group(2).strip()
    missing = [index for index in expected if index not in found]
    if missing:
        logger.warning(
            f"الترجمة أعادت {len(found)} بطاقة من {len(expected)} — "
            "تُستعمل بطاقات هذه الدفعة بنصّها الأصلي.")
        return None
    return found


def translate_cues(cues: Sequence[Tuple[float, float, str]],
                   provider: LLMProvider,
                   config: TranslationConfig
                   ) -> List[Tuple[float, float, str]]:
    """يترجم النصوص ويُعيدها على التوقيتات الأصلية بلا مسّها."""
    language = LANGUAGES.get(config.target, (config.target, config.target))[1]
    system = SYSTEM_PROMPT.format(language=language)

    numbered = [(index, start, end, text)
                for index, (start, end, text) in enumerate(cues, start=1)]
    out: List[Tuple[float, float, str]] = []

    for offset in range(0, len(numbered), config.batch_size):
        batch = numbered[offset:offset + config.batch_size]
        expected = [index for index, _s, _e, _t in batch]
        try:
            raw = complete_with_retry(
                provider, system, _render_batch(batch),
                max_tokens=3000, timeout=config.timeout_seconds)
            translated = _parse(raw, expected)
        except RewriteUnavailableError as exc:
            logger.warning(f"تعذّرت ترجمة دفعة: {exc}")
            translated = None
        except Exception as exc:
            logger.warning(f"عطل غير متوقّع في الترجمة: {exc}")
            translated = None

        for index, start, end, text in batch:
            # السقوط إلى النصّ الأصلي لا إلى الحذف: ملفّ ترجمة فيه فجوة
            # يبدو معطوبًا، وفيه سطرٌ بالعربية يبدو — وهو كذلك — سطرًا
            # لم يُترجَم.
            out.append((start, end,
                        (translated or {}).get(index) or text))
    return out


def render_srt(cues: Sequence[Tuple[float, float, str]]) -> str:
    return "\n".join(
        f"{index}\n{_stamp(start, ',')} --> {_stamp(end, ',')}\n{_wrap(text)}\n"
        for index, (start, end, text) in enumerate(cues, start=1))


def render_vtt(cues: Sequence[Tuple[float, float, str]]) -> str:
    blocks = ["WEBVTT\n"]
    for start, end, text in cues:
        blocks.append(f"{_stamp(start, '.')} --> {_stamp(end, '.')}\n"
                      f"{_wrap(text)}\n")
    return "\n".join(blocks)


def translate_subtitles(transcript: TranscriptionResult, base_path: Path,
                        provider: LLMProvider,
                        config: TranslationConfig,
                        formats: Sequence[str] = ("srt", "vtt")) -> List[Path]:
    """يكتب ``<الاسم>.<الرمز>.srt`` بجوار المستند ويعيد ما كُتب."""
    cues = build_cues(transcript)
    if not cues:
        return []

    translated = translate_cues(cues, provider, config)
    renderers = {"srt": render_srt, "vtt": render_vtt}
    written: List[Path] = []
    for name in formats:
        render = renderers.get(name.lower())
        if render is None:
            continue
        # ‏``.en.srt`` لا ``.srt``: المشغّلات تقرأ رمز اللغة من الاسم
        # فتعرض «English» في قائمة الترجمات بدل «Track 2».
        path = base_path.with_suffix(f".{config.target}.{name.lower()}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(translated), encoding="utf-8")
        written.append(path)
    if written:
        label = LANGUAGES.get(config.target, (config.target,))[0]
        logger.info(f"ترجمة إلى {label}: "
                    + "، ".join(p.name for p in written))
    return written
