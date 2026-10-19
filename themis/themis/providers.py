"""Capa de proveedores: un cliente unificado para APIs compatibles con OpenAI y Anthropic."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import httpx


@dataclass
class ModelSpec:
    provider: str
    id: str
    tier: str = "paid"            # free | paid | local
    tags: list[str] = field(default_factory=list)
    ctx: int = 32000
    cost_in: float = 0.0          # USD por millón de tokens
    cost_out: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.id}"


@dataclass
class LLMResponse:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0


class ProviderError(Exception):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


class Provider:
    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.kind = cfg.get("kind", "openai")
        self.base_url = cfg["base_url"].rstrip("/")
        self.api_key_env = cfg.get("api_key_env")
        self.privacy = cfg.get("privacy", "may_train")
        self.models = [ModelSpec(provider=name, **m) for m in cfg.get("models", [])]
        self.cooldown_until = 0.0

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) if self.api_key_env else None

    @property
    def available(self) -> bool:
        if time.time() < self.cooldown_until:
            return False
        # Los locales no necesitan clave; los remotos sí.
        return self.privacy == "local" or bool(self.api_key)

    async def chat(self, model: ModelSpec, system: str, messages: list[dict],
                   temperature: float = 0.2, max_tokens: int = 4096,
                   json_mode: bool = False, timeout: float = 180.0) -> LLMResponse:
        t0 = time.time()
        async with httpx.AsyncClient(timeout=timeout) as client:
            if self.kind == "anthropic":
                r = await client.post(
                    f"{self.base_url}/v1/messages",
                    headers={"x-api-key": self.api_key or "", "anthropic-version": "2023-06-01"},
                    json={"model": model.id, "system": system, "messages": messages,
                          "max_tokens": max_tokens, "temperature": temperature},
                )
                self._check(r)
                d = r.json()
                text = "".join(b.get("text", "") for b in d.get("content", []))
                tin = d.get("usage", {}).get("input_tokens", 0)
                tout = d.get("usage", {}).get("output_tokens", 0)
            else:
                headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
                body = {"model": model.id, "temperature": temperature, "max_tokens": max_tokens,
                        "messages": [{"role": "system", "content": system}, *messages]}
                if json_mode:
                    body["response_format"] = {"type": "json_object"}
                r = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
                self._check(r)
                d = r.json()
                text = d["choices"][0]["message"]["content"] or ""
                tin = d.get("usage", {}).get("prompt_tokens", 0)
                tout = d.get("usage", {}).get("completion_tokens", 0)
        cost = (tin * model.cost_in + tout * model.cost_out) / 1_000_000
        return LLMResponse(text, model.key, tin, tout, cost, time.time() - t0)

    def _check(self, r: httpx.Response) -> None:
        if r.status_code >= 400:
            raise ProviderError(f"{self.name} HTTP {r.status_code}: {r.text[:300]}", r.status_code)

    async def discover(self) -> list[str]:
        """Lista los modelos reales que ofrece la cuenta (los IDs cambian con frecuencia)."""
        async with httpx.AsyncClient(timeout=30) as client:
            if self.kind == "anthropic":
                r = await client.get(f"{self.base_url}/v1/models",
                                     headers={"x-api-key": self.api_key or "",
                                              "anthropic-version": "2023-06-01"})
            else:
                headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
                r = await client.get(f"{self.base_url}/models", headers=headers)
            self._check(r)
            return sorted(m.get("id", "") for m in r.json().get("data", []))
