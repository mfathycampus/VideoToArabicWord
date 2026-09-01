"""سجلّ محرّكات التفريغ المتاحة."""
from __future__ import annotations

from typing import Dict, List, Optional

from audio.engines.base import ASREngine, EngineInfo
from core.exceptions import ModelUnavailableError

# مرتّبة كما تُعرض في الواجهة: الافتراضي أولًا
ENGINE_ORDER = ("faster-whisper", "cohere-arabic")


def available_engines() -> List[EngineInfo]:
    infos: List[EngineInfo] = []
    for name in ENGINE_ORDER:
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
