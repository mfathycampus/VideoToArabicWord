"""سجلّ محرّكات التفريغ المتاحة."""
from __future__ import annotations

from typing import List

from audio.engines.base import ASREngine, EngineInfo
from core.exceptions import ModelUnavailableError

# المحرّك الافتراضي وحده يُعرض ويُفحص.
#
# المحرّكات الاختيارية تجرّ PyTorch، ووجودها في هذه القائمة كان يكفي
# لتحميله في العملية عند كل إقلاع — فتتنازع بيئتا OpenMP (‏PyTorch
# و CTranslate2) الأنويةَ ويتضاعف زمن التفريغ على المعالج، **دون أن
# يختار المستخدم المحرّك أصلًا**. الظهور صار صريحًا لا افتراضيًا.
ENGINE_ORDER = ("faster-whisper",)

# متاحة بالاسم من الإعداد أو سطر الأوامر، وتُعرض في الواجهة فقط عند
# رفع ``whisper.show_optional_engines``.
OPTIONAL_ENGINES = ("cohere-arabic",)


def listed_engines(include_optional: bool = False) -> tuple[str, ...]:
    """أسماء المحرّكات المعروضة في الواجهة."""
    return ENGINE_ORDER + (OPTIONAL_ENGINES if include_optional else ())


def available_engines(include_optional: bool = False) -> List[EngineInfo]:
    infos: List[EngineInfo] = []
    for name in listed_engines(include_optional):
        try:
            infos.append(build_engine(name).info)
        except Exception:
            continue
    return infos


def build_engine(name: str, **kwargs) -> ASREngine:
    """ينشئ محرّكًا بالاسم. الاستيراد كسول: محرّك غير مثبّت لا يمنع البقية."""
    if name in ("faster-whisper", "whisper", ""):
        from audio.engines.faster_whisper_engine import FasterWhisperEngine
        return FasterWhisperEngine(**kwargs)
    if name in ("cohere-arabic", "cohere"):
        from audio.engines.cohere_engine import CohereArabicEngine
        return CohereArabicEngine(**kwargs)
    raise ModelUnavailableError(f"محرّك تفريغ غير معروف: {name}")
