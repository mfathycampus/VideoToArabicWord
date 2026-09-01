from __future__ import annotations
import logging, sys
from pathlib import Path

logger = logging.getLogger("video_ai_doc")


class _JobFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for attr, default in (("job_id", "-"), ("stage", "-")):
            if not hasattr(record, attr):
                setattr(record, attr, default)
        return True


def setup_logger(log_dir: Path, level: str = "INFO") -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.setLevel(level)
    logger.handlers.clear()
    logger.addFilter(_JobFilter())

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-5s | JOB=%(job_id)s | STAGE=%(stage)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stream = logging.StreamHandler(sys.stderr); stream.setFormatter(fmt)
    file = logging.FileHandler(log_dir / "app.log", encoding="utf-8"); file.setFormatter(fmt)
    logger.addHandler(stream); logger.addHandler(file)
    return logger


setup_logger(Path("logs"))
