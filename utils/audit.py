"""سجلّ التدقيق — وحقلٌ واحد فيه هو سبب وجوده كلّه.

**السؤال الذي يُسأل في كل مراجعة امتثال:** هل غادرت بيانات المحاضرة
هذا الجهاز؟ وهو سؤالٌ لا يكفي فيه قولُ «البرنامج محلّي»، لأن البرنامج
**ليس محلّيًا بالكامل**: إعادة الصياغة والحزمة التعليمية تُرسلان نصّ
التفريغ إلى مزوّد سحابي **إن اختار المستخدم ذلك**. فالجواب الصادق
يختلف من مهمّة إلى مهمّة، ولا يعرفه إلا السجلّ.

ولذلك يحمل كل سطر هنا ``text_left_device`` صراحةً، ومعه اسم المزوّد
وما أُرسل إليه. سطرٌ يقول ``false`` هو إثباتٌ قابل للتدقيق، وسطرٌ يقول
``true`` مع اسم المزوّد أصدق من صمتٍ يُفترض فيه الخير.

**الصيغة JSONL إلحاقية** لا JSON واحدًا: الملفّ ينمو بلا قراءة ما
سبق، ولا تُفسده عمليّة كتابة مقطوعة (يسقط السطر الأخير وحده لا الملفّ
كلّه)، وتقرؤه أدوات التحليل سطرًا سطرًا.

**ولا يحمل السجلّ نصّ المحاضرة إطلاقًا** — أسماء ملفّات وأزمنة وأرقام
فقط. سجلُّ تدقيق يحوي المحتوى يصير هو نفسه تسريبًا للبيانات التي
يُفترض أن يحرسها.
"""
from __future__ import annotations

import getpass
import json
import os
import platform
import socket
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from utils.logger import logger
from version import APP_VERSION

AUDIT_FILENAME = "audit.jsonl"

#: مزوّدات تُخرج النصّ من الجهاز. ``ollama`` ليس منها — محلّي بالكامل.
#: القائمة صريحة لا مشتقّة من ``is_local``: حقلٌ يقرأه مدقّق امتثال لا
#: يجوز أن يعتمد على صفةٍ قد يغيّرها تعديلٌ في وحدة أخرى بلا انتباه.
CLOUD_PROVIDERS = {"anthropic", "openai", "openai_compatible", "cohere"}


def default_audit_dir() -> Path:
    """مجلد السجلّ المركزي — يجمع كل مهامّ الجهاز في ملفّ واحد."""
    override = os.environ.get("VTAD_AUDIT_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".config" / "video_ai_doc" / "audit"


@dataclass
class AuditRecord:
    """سطرٌ واحد. الحقول مسطّحة عمدًا ليقرأها Excel بلا معالجة."""
    source_name: str = ""
    source_bytes: int = 0
    duration_seconds: float = 0.0
    job_dir: str = ""
    outputs: List[str] = field(default_factory=list)
    engine: str = ""
    model: str = ""
    #: القرار المُدقَّق — يُملأ من سجلّ الخروج الفعلي عند نهاية المهمّة
    #: لا من الإعداد عند بدايتها (ADR-020).
    text_left_device: Optional[bool] = None
    cloud_providers: List[str] = field(default_factory=list)
    #: مزوّدات **مسموح بها** في الإعداد — نيّة لا شهادة. تُذكر لأن
    #: الفرق بينها وبين ``cloud_providers`` هو نفسه معلومة تدقيق:
    #: «كان مسموحًا ولم يُستعمل».
    permitted_providers: List[str] = field(default_factory=list)
    #: حاول الإرسال وفشل قبل أن يغادر شيء. ليس خرقًا، لكنه ليس صمتًا.
    attempted_no_egress: bool = False
    status: str = "started"          # started | completed | failed | cancelled
    error: str = ""
    elapsed_seconds: float = 0.0

    def as_line(self) -> Dict:
        return {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "app_version": APP_VERSION,
            "user": _current_user(),
            "host": _hostname(),
            "os": f"{platform.system()} {platform.release()}",
            "source_name": self.source_name,
            "source_bytes": self.source_bytes,
            "duration_seconds": round(self.duration_seconds, 2),
            "job_dir": self.job_dir,
            "outputs": self.outputs,
            "engine": self.engine,
            "model": self.model,
            "text_left_device": self.text_left_device,
            "cloud_providers": self.cloud_providers,
            "permitted_providers": self.permitted_providers,
            "attempted_no_egress": self.attempted_no_egress,
            "status": self.status,
            "error": self.error[:300],
            "elapsed_seconds": round(self.elapsed_seconds, 1),
        }


def _current_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        # ``getuser`` يرمي على حساب خدمة بلا متغيّرات بيئة — وهو
        # بالضبط الحساب الذي تعمل تحته مهمّة مجدولة ليلًا.
        return os.environ.get("USERNAME") or os.environ.get("USER") or "?"


def _hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "?"


def cloud_providers_in_use(config) -> List[str]:
    """أسماء المزوّدات السحابية المفعّلة فعلًا في هذا الإعداد.

    الشرط مزدوج: مُفعَّلة **و** سحابية. إعادة صياغة معطّلة بمزوّد
    ``anthropic`` لا تُخرج حرفًا، وعدّها هنا يُنتج سجلًّا يقول إن النصّ
    غادر الجهاز وهو لم يغادر — وهذا أسوأ من لا سجلّ.
    """
    names: List[str] = []
    for section in ("rewrite", "study"):
        settings = getattr(config, section, None)
        if settings is None or not getattr(settings, "enabled", False):
            continue
        provider = str(getattr(settings, "provider", "")).strip().lower()
        if provider in CLOUD_PROVIDERS and provider not in names:
            names.append(provider)
    return names


def write(record: AuditRecord, *extra_dirs: Path) -> List[Path]:
    """يُلحق السطر بالسجلّ المركزي وبكل مجلد إضافي. لا يرمي أبدًا."""
    line = json.dumps(record.as_line(), ensure_ascii=False) + "\n"
    written: List[Path] = []
    targets = [default_audit_dir(), *[Path(d) for d in extra_dirs if d]]
    for directory in targets:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / AUDIT_FILENAME
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line)
            written.append(path)
        except Exception as exc:
            # سجلّ التدقيق لا يُفشل مهمّة. مجلدٌ للقراءة فقط على جهاز
            # مُدار شائع، وإسقاط المعالجة عليه عقابٌ بلا فائدة.
            logger.warning(f"تعذّرت كتابة سجلّ التدقيق في {directory}: {exc}")
    return written


@contextmanager
def record_job(config, source: Path, job_dir: Optional[Path] = None,
               enabled: bool = True):
    """يسجّل مهمّة من بدايتها إلى نهايتها مهما كانت نهايتها.

    ``finally`` لا ``else``: مهمّة أُلغيت أو سقطت **يجب** أن تُسجَّل.
    السجلّ الذي لا يحوي إلا النجاحات لا يصلح للتدقيق أصلًا — وأوّل ما
    يُسأل عنه هو ما لم ينجح.
    """
    from ai import providers as provider_layer

    # الصفحة البيضاء أولًا: ما يُسجَّل بعدها هو ما جرى في هذه المهمّة.
    provider_layer.reset_egress()

    permitted = cloud_providers_in_use(config)
    record = AuditRecord(
        source_name=Path(source).name,
        permitted_providers=permitted,
        cloud_providers=[],
        text_left_device=False,
        engine=str(getattr(getattr(config, "whisper", None), "engine", "")),
        model=str(getattr(getattr(config, "whisper", None), "model_size", "")),
        job_dir=str(job_dir or ""))
    try:
        record.source_bytes = Path(source).stat().st_size
    except Exception:
        pass

    started = time.monotonic()
    try:
        yield record
        if record.status == "started":
            record.status = "completed"
    except BaseException as exc:                      # noqa: BLE001
        record.status = ("cancelled"
                         if type(exc).__name__ == "PipelineCancelledError"
                         else "failed")
        record.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        record.elapsed_seconds = time.monotonic() - started
        # الحقيقة تُقرأ من حدود الشبكة عند النهاية — حتى عند السقوط:
        # مهمّة أرسلت ثم سقطت **أرسلت**، وإغفال ذلك أخطر من إغفال نجاح.
        try:
            egress = provider_layer.egress_report()
            record.text_left_device = bool(egress["sent"])
            record.cloud_providers = list(egress["providers"])
            record.attempted_no_egress = bool(
                egress["attempts"]) and not egress["sent"]
        except Exception as exc:
            logger.warning(f"تعذّرت قراءة سجلّ الخروج: {exc}")
        if enabled:
            write(record, Path(record.job_dir) if record.job_dir else None)
