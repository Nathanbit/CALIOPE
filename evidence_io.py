"""Shared, format-neutral primitives for CALIOPE's evidence modules.

One owner for the low-level pieces that every ledger needs: canonical JSON,
strict JSON reading, atomic writes, exclusive locks, timezone-aware time
parsing and the ``sha256sum`` manifest format. Each function takes the
caller's error class so modules keep their own error types and messages.

Hash output is unchanged for every existing record: modules choose upper or
lower case hex themselves, exactly as before.

Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, NamedTuple

HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


def canonical_text(value: Any, error_cls: type[Exception], message: str) -> str:
    """Sorted keys, compact separators, UTF-8 kept, NaN/infinity refused."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise error_cls(f"{message}: {exc}") from None


def canonical_bytes(value: Any, error_cls: type[Exception], message: str) -> bytes:
    return canonical_text(value, error_cls, message).encode("utf-8")


def sha256_hex(data: bytes, *, upper: bool) -> str:
    digest = hashlib.sha256(data).hexdigest()
    return digest.upper() if upper else digest


def loads_strict(text: str, error_cls: type[Exception]) -> Any:
    """json.loads that refuses NaN/Infinity instead of producing floats."""
    def reject_constant(value: str) -> None:
        raise error_cls(f"non-finite JSON number is forbidden: {value}")

    return json.loads(text, parse_constant=reject_constant)


def read_json_object(path: Path, error_cls: type[Exception]) -> dict:
    value = loads_strict(path.read_text(encoding="utf-8"), error_cls)
    if not isinstance(value, dict):
        raise error_cls(f"{path}: JSON root must be an object")
    return value


def atomic_write_json(path: Path, value: dict, error_cls: type[Exception], message: str) -> None:
    """Write next to the target and rename, so readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical_bytes(value, error_cls, message) + b"\n")
    temporary.replace(path)


@contextmanager
def exclusive_lock(lock: Path, error_cls: type[Exception], message: str,
                   timeout: float = 0.0) -> Iterator[None]:
    """O_EXCL lock file. ``timeout`` 0 fails at once; otherwise retries."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise error_cls(message) from None
            time.sleep(0.05)
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        yield
    finally:
        os.close(descriptor)
        lock.unlink(missing_ok=True)


def parse_aware_time(value: Any, field: str, error_cls: type[Exception]) -> datetime:
    """ISO-8601 with a timezone, returned in UTC. Naive times are refused."""
    if not isinstance(value, str):
        raise error_cls(f"{field}: not an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise error_cls(f"{field}: not an ISO-8601 timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise error_cls(f"{field}: timezone required (naive datetimes are rejected)")
    return parsed.astimezone(timezone.utc)


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class ManifestEntry(NamedTuple):
    line: int
    digest: str | None  # lower-case hex, None when the line is malformed
    path: str


def parse_sha256_manifest(path: Path) -> list[ManifestEntry]:
    """``<64 hex>  <relative path>`` per line (``sha256sum`` format, optional
    ``*`` binary marker). Malformed lines are returned with ``digest=None`` so
    callers can fail them instead of skipping them."""
    entries = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or not HEX64.match(parts[0]):
            entries.append(ManifestEntry(number, None, line.strip()))
            continue
        entries.append(ManifestEntry(number, parts[0].lower(), parts[1].strip().lstrip("*")))
    return entries
