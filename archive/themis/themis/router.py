"""Router: elige el mejor modelo disponible según necesidades, costo y confidencialidad."""
from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

import yaml

from .providers import LLMResponse, ModelSpec, Provider, ProviderError

SAFE_PRIVACY = {"local", "no_train"}


class BudgetExceeded(Exception):
    pass


class Router:
    def __init__(self, config_path: str | Path = "config.yaml"):
        cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        self.policy = cfg.get("policy", {})
        self.providers = {n: Provider(n, c) for n, c in cfg["providers"].items()}
        self.agent_cfg = cfg.get("agents", {})
        self.spent = 0.0
        self._db = None
        if self.policy.get("cache", True):
            Path("runs").mkdir(exist_ok=True)
            self._db = sqlite3.connect("runs/cache.sqlite")
            self._db.execute("CREATE TABLE IF NOT EXISTS c(k TEXT PRIMARY KEY, ts REAL, text TEXT, model TEXT)")

    def _ckey(self, agent, system, messages, sensitivity, mode, json_mode) -> str:
        raw = repr((agent, system, messages, sensitivity, mode, json_mode))
        return hashlib.sha256(raw.encode()).hexdigest()

    # ---------- selección ----------
    def candidates(self, needs: list[str], sensitivity: str, mode: str | None = None,
                   exclude: set[str] | None = None) -> list[tuple[Provider, ModelSpec]]:
        mode = mode or self.policy.get("mode", "balanced")
        exclude = exclude or set()
        scored = []
        for p in self.providers.values():
            if not p.available:
                continue
            if sensitivity == "confidential" and p.privacy not in SAFE_PRIVACY:
                continue  # nunca enviar material confidencial a niveles que puedan entrenar
            for m in p.models:
                if m.key in exclude:
                    continue
                match = sum(1 for n in needs if n in m.tags)
                quality = match * 10 + (5 if m.tier == "paid" else 0) + min(m.ctx / 200000, 5)
                cost_pen = (m.cost_in + m.cost_out) / 10
                if mode == "free_first":
                    score = match * 10 - (30 if m.tier == "paid" else 0) + (8 if m.tier == "local" else 0)
                elif mode == "quality_first":
                    score = quality
                else:  # balanced
                    score = quality - cost_pen + (4 if m.tier in ("free", "local") else 0)
                scored.append((score, p, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [(p, m) for _, p, m in scored]

    # ---------- ejecución con fallback ----------
    async def complete(self, agent: str, system: str, messages: list[dict], *,
                       sensitivity: str, mode: str | None = None, json_mode: bool = False,
                       max_tokens: int = 4096, temperature: float = 0.2,
                       exclude: set[str] | None = None) -> LLMResponse:
        if self.spent >= self.policy.get("max_cost_usd_per_run", 2.0):
            raise BudgetExceeded(f"Presupuesto agotado (${self.spent:.2f})")
        ck = self._ckey(agent, system, messages, sensitivity, mode, json_mode)
        if self._db and temperature <= 0.3:
            row = self._db.execute("SELECT text, model FROM c WHERE k=? AND ts>?",
                                   (ck, time.time() - self.policy.get("cache_ttl_seconds", 3600))).fetchone()
            if row:
                return LLMResponse(row[0], f"cache:{row[1]}")
        needs = self.agent_cfg.get(agent, {}).get("needs", [])
        cands = self.candidates(needs, sensitivity, mode, exclude)
        if not cands:
            raise ProviderError(
                f"Sin proveedores disponibles para sensibilidad '{sensitivity}'. "
                "Configura un modelo local (Ollama/LM Studio) o una API con no_train."
            )
        last: Exception | None = None
        for p, m in cands[:6]:
            try:
                resp = await p.chat(m, system, messages, temperature, max_tokens, json_mode)
                self.spent += resp.cost_usd
                if self._db:
                    self._db.execute("INSERT OR REPLACE INTO c VALUES(?,?,?,?)", (ck, time.time(), resp.text, resp.model))
                    self._db.commit()
                return resp
            except ProviderError as e:
                last = e
                if e.status in (429, 500, 502, 503, 529):
                    p.cooldown_until = time.time() + self.policy.get("cooldown_seconds", 60)
            except Exception as e:  # red, timeout, etc.
                last = e
        raise ProviderError(f"Todos los candidatos fallaron. Último error: {last}")
