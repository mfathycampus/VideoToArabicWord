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

SOURCE_FINGERPRINT_VERSION = "3"
PROCESSING_FINGERPRINT_VERSION = "1"

#: Hex characters kept from every digest. Job directory names carry a
#: source fingerprint as a ``__<hex>`` suffix, and ``utils.timestamps.
#: strip_source_fingerprint`` must strip exactly that many — it is
#: imported from here so the two can never drift apart again.
FINGERPRINT_LENGTH = 20


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
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


def source_fingerprint(path: Path) -> str:
    r"""Fingerprint a source without reading an entire multi-GB video.

    Identity is **content-based**: file name, size, and hashes of the first
    and last megabyte. Small files are hashed completely.

    Version 3 dropped the absolute path and the nanosecond mtime, and that
    was a bug fix, not a tidy-up. Both change under OneDrive for reasons
    that have nothing to do with the file:

    * Folder redirection moves ``C:\Users\x\Desktop`` to
      ``C:\Users\x\OneDrive\Desktop``. Same file, new absolute path.
    * Files On-Demand evicts a file to the cloud and rehydrates it on next
      read, which rewrites mtime. Same bytes, new mtime.

    Either one changed the fingerprint, which changed the job directory,
    which discarded every cached stage — so a three-hour lecture was
    transcribed again from zero. School tenants redirect Desktop and
    Documents by default, so this hit the intended audience hardest.

    What is lost: a file edited in place to *exactly* the same size with an
    identical first and last megabyte is now treated as unchanged. For
    video that is not a realistic edit. What is gained: moving or renaming
    a folder, or letting OneDrive do its job, no longer throws away hours
    of work — and the same source in two folders now resumes instead of
    starting over.

    The file name is kept so ``Lecture.mp4`` and ``Lecture.mkv`` stay
    distinct even when one is a container remux of the other.
    """
    path = Path(path)
    digest = hashlib.sha256()
    try:
        stat = path.stat()
    except OSError:
        # The pipeline still needs a stable job directory so validation can
        # report a friendly "file not found" error instead of failing while
        # constructing the job identity.
        digest.update(f"v{SOURCE_FINGERPRINT_VERSION}|missing|{path.name}".encode("utf-8"))
        return digest.hexdigest()[:FINGERPRINT_LENGTH]

    size = stat.st_size
    sample_size = min(1 << 20, size)
    digest.update(f"v{SOURCE_FINGERPRINT_VERSION}|{path.name}|{size}".encode("utf-8"))
    with path.open("rb") as handle:
        if sample_size:
            digest.update(handle.read(sample_size))
            if size > sample_size:
                handle.seek(max(0, size - sample_size))
                digest.update(handle.read(sample_size))
    return digest.hexdigest()[:FINGERPRINT_LENGTH]


def processing_fingerprint(*parts: Any) -> str:
    """Hash stage inputs/configuration into a compact reusable identifier."""
    payload = [PROCESSING_FINGERPRINT_VERSION, *parts]
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]
