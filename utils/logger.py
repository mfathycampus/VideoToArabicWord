"""السجلّات — بلا أي أثر على القرص وقت الاستيراد.

النسخة السابقة كانت تنفّذ ``setup_logger(Path("logs"))`` في آخر الملف،
أي عند أول استيراد لأي وحدة في المشروع. النتيجة: مجلد ``logs/`` وملف
مفتوح داخل **مجلد العمل الحالي** أيًا كان — مجلد المستخدم، أو مجلد
الاختبارات، أو ``Program Files`` في تثبيت مُغلَّف حيث تفشل الكتابة أصلًا.
ثم يُستدعى الإعداد ثانيةً بالمسار الصحيح فيبقى الملف الأول يتيمًا.

الآن: الاستيراد يهيّئ مخرجًا نصيًا إلى ``stderr`` فقط (بلا قرص)، وملف
السجلّ لا يُفتح إلا بطلب صريح عبر ``setup_logger(log_dir)`` من نقطة دخول.
"""
from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

logger = logging.getLogger("video_ai_doc")

_FORMAT = ("%(asctime)s | %(levelname)-5s | JOB=%(job_id)s | "
           "STAGE=%(stage)s | %(message)s")
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# اسم ثابت للمخرجات حتى نستبدلها بدل تكديسها عند إعادة الإعداد
_STREAM_HANDLER = "video_ai_doc.stream"
_FILE_HANDLER = "video_ai_doc.file"
_LOG_MAX_BYTES = 10 * 1024 * 1024
_LOG_BACKUP_COUNT = 5
_SECRET_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)(hf_[A-Za-z0-9._-]+)"),
    re.compile(r"(?i)(sk-[A-Za-z0-9._-]+)"),
    re.compile(r"(?i)(token\s*[:=]\s*)[^\s,;]+"),
]

def redact_secrets(text: object) -> str:
    """Redact common credential/token patterns before they reach logs."""
    value = str(text)
    value = _SECRET_PATTERNS[0].sub(r"\1[REDACTED]", value)
    value = _SECRET_PATTERNS[1].sub(r"\1[REDACTED]", value)
    value = _SECRET_PATTERNS[2].sub("[REDACTED_HF_TOKEN]", value)
    value = _SECRET_PATTERNS[3].sub("[REDACTED_API_KEY]", value)
    value = _SECRET_PATTERNS[4].sub(r"\1[REDACTED]", value)
    return value

class _SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_secrets(record.getMessage())
        record.args = ()
        return True


class _JobFilter(logging.Filter):
    """يملأ الحقول المخصّصة حين لا يمرّرها المُسجِّل.

    ‏``JOB`` و ``STAGE`` تُملآن من ``JobLogContext`` أثناء التشغيل؛
    وخارجه تبقى ``-`` بدل أن ينهار التنسيق بـ ``KeyError``.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        for attr, default in (("job_id", _context["job_id"]),
                              ("stage", _context["stage"])):
            if not hasattr(record, attr):
                setattr(record, attr, default)
        return True


# سياق التشغيل الحالي — يُحدَّث من الـ pipeline عبر ``bind_job``
_context = {"job_id": "-", "stage": "-"}


def bind_job(job_id: Optional[str] = None, stage: Optional[str] = None) -> None:
    """يربط رقم المهمة والمرحلة بكل سطر سجلّ لاحق.

    بدونه يبقى عقد السجلّات في المواصفة §25 حبرًا على ورق: الحقلان
    موجودان في التنسيق ولا يملؤهما أحد.
    """
    if job_id is not None:
        _context["job_id"] = job_id or "-"
    if stage is not None:
        _context["stage"] = stage or "-"


def clear_job() -> None:
    """يعيد السياق إلى حالته المحايدة بعد انتهاء المهمة."""
    _context["job_id"] = "-"
    _context["stage"] = "-"


def _drop_handler(name: str) -> None:
    for handler in list(logger.handlers):
        if getattr(handler, "name", None) == name:
            logger.removeHandler(handler)
            handler.close()


def _install_stream_handler(level: str = "INFO") -> None:
    """مخرج نصّي إلى stderr — بلا أي كتابة على القرص."""
    _drop_handler(_STREAM_HANDLER)
    handler = logging.StreamHandler(sys.stderr)
    handler.name = _STREAM_HANDLER
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    handler.addFilter(_SecretRedactionFilter())
    logger.addHandler(handler)
    logger.setLevel(level)


def setup_logger(log_dir: Optional[Path] = None,
                 level: str = "INFO") -> logging.Logger:
    """يهيّئ السجلّ. ملف السجلّ يُفتح فقط عند تمرير ``log_dir``.

    قابلة للاستدعاء أكثر من مرة: تستبدل المخرجات ولا تكدّسها.
    """
    _install_stream_handler(level)
    _drop_handler(_FILE_HANDLER)

    if log_dir is not None:
        try:
            log_dir = Path(log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                log_dir / "app.log", maxBytes=_LOG_MAX_BYTES,
                backupCount=_LOG_BACKUP_COUNT, encoding="utf-8")
            handler.name = _FILE_HANDLER
            handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
            handler.addFilter(_SecretRedactionFilter())
            logger.addHandler(handler)
        except OSError as exc:
            # مجلد للقراءة فقط أو بلا صلاحية: نواصل على stderr بدل الانهيار
            logger.warning(f"تعذّر فتح ملف السجلّ في {log_dir}: {exc}")

    return logger


# تهيئة أولية بلا أثر على القرص: stderr فقط.
logger.handlers.clear()
logger.addFilter(_JobFilter())
_install_stream_handler()
logger.propagate = False
