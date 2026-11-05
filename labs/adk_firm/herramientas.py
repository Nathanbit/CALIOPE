"""Herramientas de la firma ADK: las CLI de CALIOPE con argumentos fijos.

Este módulo no importa ADK: es la frontera que se prueba sin red ni modelos.
Cada herramienta llama a una CLI existente (`prediction_ledger.py`,
`python -m desk`, `tools/fetch_klines.py`), que sigue siendo la única dueña de
su formato y de sus compuertas. Aquí solo se añade lo que un modelo no debe
poder decidir:

- El autor va atado a cada analista al construirlo; el modelo no lo elige.
- No existe ninguna herramienta para aprobar, enviar, rechazar, apagar o
  reactivar la mesa. La aprobación sigue siendo `python -m desk approve`, en la
  terminal de la persona.
- Los argumentos se validan antes de llamar a la CLI y todo fallo devuelve
  `ok: false` (falla cerrado). Un código de salida distinto de cero nunca se
  presenta como éxito.
- Las claves de BloFin y `CALIOPE_DESK_LIVE` solo llegan a la CLI de la mesa,
  y `CALIOPE_DESK_LIVE` nunca: la firma ADK opera solo en demo.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
LEDGER = "projects/macroflow/predictions/ledger.jsonl"
RUNS = Path("runs") / "adk"            # ignorado por git (.gitignore: runs/)
TIMEOUT_SECONDS = 180
MAX_OUTPUT = 12_000

QUESTION = re.compile(r"q-[0-9a-f]{16}")
ORDER = re.compile(r"o-[0-9a-f]{24}")
# Solo IAs: "nathan" es la persona y "claude" es el analista de Claude Code.
ANALYST_AUTHOR = re.compile(r"ia:[a-z0-9][a-z0-9_.-]{0,39}")
RESERVED_AUTHORS = frozenset({"ia:claude"})
DESK_SECRETS = ("BLOFIN_API_KEY", "BLOFIN_API_SECRET", "BLOFIN_API_PASSPHRASE")
LIVE_SWITCH = "CALIOPE_DESK_LIVE"
# Más estricto que la política: con menos de 30 resultados, margen ≤ 10%
# (regla del analista en .claude/agents/caliope-analyst.md).
MAX_LAB_MARGIN = 0.10
MAX_LAB_LEVERAGE = 5

Runner = Callable[[list[str], dict, Path], subprocess.CompletedProcess]


def _default_runner(argv: list[str], env: dict, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=TIMEOUT_SECONDS, check=False)


def _env(desk: bool) -> dict:
    env = {k: v for k, v in os.environ.items() if k != LIVE_SWITCH}
    if not desk:
        for key in DESK_SECRETS:
            env.pop(key, None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _fail(error: str) -> dict:
    return {"ok": False, "error": error}


def _parse(text: str) -> object:
    try:
        return json.loads(text)
    except ValueError:
        return text[-MAX_OUTPUT:]


@dataclass
class Caliope:
    """Ejecuta las CLI desde la raíz del repositorio. `runner` se cambia en pruebas."""

    root: Path = ROOT
    ledger: str = LEDGER
    runner: Runner = _default_runner

    def run(self, args: list[str], *, desk: bool = False) -> dict:
        argv = [sys.executable, "-m", "desk", *args] if desk else [sys.executable, *args]
        try:
            done = self.runner(argv, _env(desk), self.root)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return _fail(f"no se pudo ejecutar: {exc}")
        out = _parse(done.stdout)
        if done.returncode != 0:
            return {"ok": False, "exit_code": done.returncode, "output": out,
                    "error": (done.stderr or "").strip()[-MAX_OUTPUT:] or "la CLI falló"}
        return {"ok": True, "output": out}

    def ledger_cli(self, *args: str) -> dict:
        return self.run(["prediction_ledger.py", "--ledger", self.ledger, *args])

    # --- lectura -----------------------------------------------------------

    def agenda(self) -> dict:
        """Preguntas que cerraron sin resolver y las que abren pronto."""
        return self.ledger_cli("due")

    def historial(self) -> dict:
        """Cada autor contra los rivales (historia, volatilidad, bootstrap)."""
        return self.ledger_cli("rival")

    def estado(self, author: str, question_id: str | None = None) -> dict:
        args = ["state", "--author", author]
        if question_id is not None:
            if not QUESTION.fullmatch(question_id):
                return _fail("question_id debe tener la forma q-<16 hex>")
            args += ["--question", question_id]
        return self.ledger_cli(*args)

    def mesa_estado(self) -> dict:
        return self.run(["status"], desk=True)

    def mesa_verificar(self) -> dict:
        return self.run(["verify"], desk=True)

    def velas(self, symbol: str, interval: str, hours: float) -> dict:
        """Velas públicas de Binance de las últimas horas, resumidas."""
        if symbol != "BTCUSDT":
            return _fail("por ahora solo BTCUSDT")
        if interval not in ("1m", "5m", "15m", "1h"):
            return _fail("intervalo: 1m, 5m, 15m o 1h")
        if not 0 < hours <= 48:
            return _fail("hours debe estar entre 0 y 48")
        end = datetime.now(timezone.utc).replace(microsecond=0)
        start = end - timedelta(hours=hours)
        out = RUNS / f"velas_{symbol}_{interval}_{end:%Y%m%dT%H%M%SZ}.json"
        (self.root / RUNS).mkdir(parents=True, exist_ok=True)
        done = self.run(["tools/fetch_klines.py", "--symbol", symbol, "--interval", interval,
                         "--start", f"{start:%Y-%m-%dT%H:%M:%SZ}", "--end", f"{end:%Y-%m-%dT%H:%M:%SZ}",
                         "--lookback-hours", "0", "--out", str(out)])
        if not done["ok"]:
            return done
        try:
            rows = json.loads((self.root / out).read_text(encoding="utf-8"))["klines"]
        except (OSError, ValueError, KeyError) as exc:
            return _fail(f"no pude leer las velas descargadas: {exc}")
        if not rows:
            return _fail("Binance no devolvió velas")
        highs = [float(r[2]) for r in rows]
        lows = [float(r[3]) for r in rows]
        last = rows[-1]
        return {"ok": True, "fuente": f"Binance {symbol} {interval}", "archivo": str(out),
                "velas": len(rows), "ultimo_cierre": float(last[4]),
                "ultima_vela_abre": datetime.fromtimestamp(last[0] / 1000, timezone.utc).isoformat(),
                "maximo": max(highs), "minimo": min(lows)}

    # --- escritura (siempre tras confirmación humana, ver caliope_firm/agent.py) ---

    def sellar(self, author: str, concept: dict, probability: float, rationale: str,
               *, dry_run: bool) -> dict:
        if not ANALYST_AUTHOR.fullmatch(author) or author in RESERVED_AUTHORS:
            return _fail(f"autor no permitido para la firma ADK: {author}")
        if not isinstance(concept, dict) or not concept:
            return _fail("concept debe ser un objeto JSON (ver concepts.py)")
        if isinstance(probability, bool) or not isinstance(probability, (int, float)) \
                or not 0.01 <= probability <= 0.99:
            return _fail("probability debe estar entre 0.01 y 0.99")
        if not isinstance(rationale, str) or not 10 <= len(rationale.strip()) <= 600:
            return _fail("rationale: entre 10 y 600 caracteres, con la evidencia y su fuente")
        args = ["seal", "--concept", json.dumps(concept, ensure_ascii=False), "--author", author,
                "--probability", repr(float(probability)), "--rationale", rationale.strip()]
        return self.ledger_cli(*args, *(["--dry-run"] if dry_run else []))

    def proponer(self, author: str, question_id: str, side: str, margin: float, leverage: int,
                 stop_loss: float, take_profit: float | None, rationale: str) -> dict:
        if not ANALYST_AUTHOR.fullmatch(author) or author in RESERVED_AUTHORS:
            return _fail(f"autor no permitido para la firma ADK: {author}")
        if not QUESTION.fullmatch(question_id or ""):
            return _fail("question_id debe tener la forma q-<16 hex>")
        if side not in ("buy", "sell"):
            return _fail("side: buy o sell")
        if isinstance(margin, bool) or not isinstance(margin, (int, float)) \
                or not 0 < margin <= MAX_LAB_MARGIN:
            return _fail(f"margin: mayor que 0 y como máximo {MAX_LAB_MARGIN} en la firma ADK")
        if isinstance(leverage, bool) or not isinstance(leverage, int) or not 1 <= leverage <= MAX_LAB_LEVERAGE:
            return _fail(f"leverage: entero de 1 a {MAX_LAB_LEVERAGE}")
        if isinstance(stop_loss, bool) or not isinstance(stop_loss, (int, float)) or stop_loss <= 0:
            return _fail("stop_loss obligatorio y positivo")
        args = ["propose", "--author", author, "--question", question_id, "--side", side,
                "--margin", repr(float(margin)), "--leverage", str(leverage), "--sl", repr(float(stop_loss)),
                "--rationale", (rationale or "").strip()[:600]]
        if take_profit is not None:
            if isinstance(take_profit, bool) or not isinstance(take_profit, (int, float)) or take_profit <= 0:
                return _fail("take_profit debe ser positivo")
            args += ["--tp", repr(float(take_profit))]
        return self.run(args, desk=True)


# Lo que ninguna versión de la firma puede llamar. Las pruebas lo comprueban.
FORBIDDEN_DESK_COMMANDS = ("approve", "submit", "reject", "kill", "unkill")
