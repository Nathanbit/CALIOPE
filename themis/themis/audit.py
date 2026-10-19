"""Bitácora de auditoría con cadena de hashes (a prueba de alteraciones)."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path


class AuditLog:
    def __init__(self, path: str | Path = "runs/audit.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.prev = "0" * 64
        if self.path.exists():
            lines = self.path.read_text(encoding="utf-8").strip().splitlines()
            if lines:
                self.prev = json.loads(lines[-1])["hash"]

    def write(self, event: str, **data) -> None:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, "prev": self.prev, **data}
        rec["hash"] = hashlib.sha256(json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest()
        self.prev = rec["hash"]
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def verify(self) -> bool:
        prev = "0" * 64
        for line in self.path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            h = rec.pop("hash")
            if rec["prev"] != prev or hashlib.sha256(
                    json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest() != h:
                return False
            prev = h
        return True
