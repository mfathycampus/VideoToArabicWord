"""Stable fingerprints for source files, processing configurations and artifacts.

The fingerprint layer is deliberately separate from the job state so every
stage can make the same resume decision: an artifact is reusable only when
its bytes and the inputs/configuration that produced it still match.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SOURCE_FINGERPRINT_VERSION = "2"
PROCESSING_FINGERPRINT_VERSION = "1"


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def config_fingerprint(config: Any, *, exclude: set[str] | None = None) -> str:
    """Return a deterministic digest for a Pydantic/dataclass/dict config."""
    if hasattr(config, "model_dump"):
        value = config.model_dump(mode="json")
    elif hasattr(config, "__dict__"):
        value = dict(config.__dict__)
    else:
        value = config
    if exclude and isinstance(value, dict):
        value = {k: v for k, v in value.items() if k not in exclude}
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()[:20]


def source_fingerprint(path: Path) -> str:
    """Fingerprint a source without reading an entire multi-GB video.

    Identity uses absolute path, size, nanosecond mtime, plus hashes of the
    beginning and end of the file. Small files are hashed completely.
    """
    path = Path(path)
    digest = hashlib.sha256()
    try:
        stat = path.stat()
    except OSError:
        # The pipeline still needs a stable job directory so validation can
        # report a friendly "file not found" error instead of failing while
        # constructing the job identity.
        digest.update(f"v{SOURCE_FINGERPRINT_VERSION}|missing|{path.resolve()}".encode("utf-8"))
        return digest.hexdigest()[:20]

    size = stat.st_size
    sample_size = min(1 << 20, size)
    digest.update(f"v{SOURCE_FINGERPRINT_VERSION}|{path.resolve()}|{size}|{stat.st_mtime_ns}".encode("utf-8"))
    with path.open("rb") as handle:
        if sample_size:
            digest.update(handle.read(sample_size))
            if size > sample_size:
                handle.seek(max(0, size - sample_size))
                digest.update(handle.read(sample_size))
    return digest.hexdigest()[:20]


def processing_fingerprint(*parts: Any) -> str:
    """Hash stage inputs/configuration into a compact reusable identifier."""
    payload = [PROCESSING_FINGERPRINT_VERSION, *parts]
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()[:20]
