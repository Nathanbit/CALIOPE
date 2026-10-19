"""Espacio de parámetros dinámico.

Cada parámetro tiene límites DUROS (nunca se cruzan) y límites de BÚSQUEDA que
el optimizador puede recentrar en cada iteración alrededor del mejor valor
(búsqueda adaptativa). Se carga desde JSON para que añadir/quitar parámetros no
requiera tocar código.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path
from typing import Any, Literal

import numpy as np
import optuna


@dataclass(frozen=True)
class ParamSpec:
    name: str
    kind: Literal["int", "float", "categorical"]
    default: Any
    hard_low: float | None = None
    hard_high: float | None = None
    low: float | None = None         # límites de búsqueda actuales
    high: float | None = None
    step: float | None = None
    log: bool = False
    choices: list | None = None
    pine_input: str | None = None    # nombre de la variable en el .pine

    def __post_init__(self):
        if self.kind != "categorical":
            if self.hard_low is None or self.hard_high is None or self.hard_low >= self.hard_high:
                raise ValueError(f"{self.name}: hard_low/hard_high inválidos")
            object.__setattr__(self, "low", self.hard_low if self.low is None else max(self.low, self.hard_low))
            object.__setattr__(self, "high", self.hard_high if self.high is None else min(self.high, self.hard_high))
        elif not self.choices:
            raise ValueError(f"{self.name}: categorical sin choices")

    def suggest(self, trial: optuna.Trial):
        if self.kind == "int":
            return trial.suggest_int(self.name, int(self.low), int(self.high), step=int(self.step or 1), log=self.log)
        if self.kind == "float":
            return trial.suggest_float(self.name, self.low, self.high, step=None if self.log else self.step, log=self.log)
        return trial.suggest_categorical(self.name, self.choices)

    def narrowed(self, center: Any, frac: float) -> "ParamSpec":
        if self.kind == "categorical":
            return self
        half = (self.hard_high - self.hard_low) * frac / 2
        lo, hi = max(self.hard_low, center - half), min(self.hard_high, center + half)
        if self.step and self.kind == "float":
            # Encajar en la rejilla hard_low + k*step para que Optuna no recorte ni avise
            k_lo = np.floor((lo - self.hard_low) / self.step + 1e-9)
            k_hi = np.floor((hi - self.hard_low) / self.step + 1e-9)
            lo, hi = round(self.hard_low + k_lo * self.step, 10), round(self.hard_low + k_hi * self.step, 10)
            if hi <= lo:
                hi = min(self.hard_high, round(lo + self.step, 10))
        if self.kind == "int":
            lo, hi = int(round(lo)), int(round(hi))
            if hi <= lo:
                hi = min(int(self.hard_high), lo + int(self.step or 1))
        return replace(self, low=lo, high=hi)


@dataclass
class ParamSpace:
    specs: dict[str, ParamSpec]
    fixed: dict[str, Any] = field(default_factory=dict)   # no se optimizan (p. ej. blocked_hours)

    @classmethod
    def from_json(cls, path: str | Path) -> "ParamSpace":
        raw = json.loads(Path(path).read_text())
        return cls({s["name"]: ParamSpec(**s) for s in raw["params"]}, raw.get("fixed", {}))

    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps({"params": [asdict(s) for s in self.specs.values()],
                                          "fixed": self.fixed}, indent=2))

    def in_range(self, params: dict) -> dict:
        """Solo los valores que caen dentro de los límites de búsqueda actuales."""
        out = {}
        for k, s in self.specs.items():
            v = params.get(k)
            if v is None:
                continue
            if s.kind == "categorical" and v in s.choices or s.kind != "categorical" and s.low <= v <= s.high:
                out[k] = v
        return out

    def defaults(self) -> dict:
        return {**{k: s.default for k, s in self.specs.items()}, **self.fixed}

    def suggest(self, trial: optuna.Trial) -> dict:
        return {**{k: s.suggest(trial) for k, s in self.specs.items()}, **self.fixed}

    def narrowed_around(self, best: dict, frac: float) -> "ParamSpace":
        return ParamSpace({k: s.narrowed(best[k], frac) if k in best else s for k, s in self.specs.items()},
                          dict(self.fixed))

    def pine_mapping(self) -> dict[str, str]:
        return {k: s.pine_input for k, s in self.specs.items() if s.pine_input}
