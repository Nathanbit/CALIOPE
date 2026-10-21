"""Marcas de tiempo UTC con un único dueño.

`utc_now_iso()` reemplaza a `datetime.utcnow().isoformat() + "Z"` (deprecado
desde Python 3.12) conservando exactamente el mismo formato, incluida la
omisión de microsegundos cuando valen cero.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
