"""Verificación por tipo de cambio (tabla de AGENTS.md) en un solo comando.

Adaptado del patrón *verification-loop* de ECC (affaan-m/ECC, MIT): en lugar de
recordar qué pruebas tocan a cada carpeta, el script mira qué cambió y ejecuta
solo las comprobaciones que corresponden. Imprime un resumen corto y declara
explícitamente lo que NO se verificó.

Uso (desde la raíz de CALIOPE):

    python tools/verify.py                 # cambios sin commit + archivos nuevos
    python tools/verify.py --base main     # todo lo que difiere de main
    python tools/verify.py --all           # todas las comprobaciones
    python tools/verify.py --json          # salida para máquinas

Código de salida: 0 si todo pasa, 1 si algo falla, 2 si hubo error de uso.
Solo ejecuta comandos locales de prueba y diagnóstico; nunca red ni brokers.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = "archive/"

# Prefijos que disparan las pruebas de la raíz (runner, ledgers, hooks).
ROOT_TEST_PREFIXES = (
    "caliope_loop.py",
    "recursive_evidence.py",
    "prediction_ledger.py",
    "forecast_scoring.py",
    "evidence_io.py",
    "caliope_doctor.py",
    "resolver.py",
    "concepts.py",
    "contracts/",
    "workflows/",
    "tests/",
    "evals/",
    "tools/",
    "security/",
    ".githooks/",
    ".claude/hooks/",
)


@dataclass(frozen=True)
class Check:
    name: str
    cmd: tuple[str, ...]
    cwd: str = "."
    requires_module: str | None = None


@dataclass
class Result:
    name: str
    status: str  # pass | fail | skipped
    detail: str = ""
    tail: list[str] = field(default_factory=list)


DOCTOR = Check("doctor", (sys.executable, "caliope_loop.py", "doctor"))
ROOT_TESTS = Check(
    "tests raíz (runner, ledgers, hooks)",
    (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"),
)
ARCHIVE_TESTS = Check(
    "tests archivados (sin mantenimiento)",
    (sys.executable, "-m", "unittest", "discover", "-s", "archive/tests", "-t", ".", "-p", "test_*.py"),
)
ALL_CHECKS = (DOCTOR, ROOT_TESTS, ARCHIVE_TESTS)


def _git(*args: str) -> list[str]:
    out = subprocess.run(("git", *args), cwd=ROOT, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip() or 'error'}")
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def changed_files(base: str | None) -> list[str]:
    """Archivos modificados respecto a HEAD (o a `base`) más los nuevos sin seguimiento."""
    files = set(_git("diff", "--name-only", f"{base}...HEAD" if base else "HEAD"))
    if base:
        files |= set(_git("diff", "--name-only", "HEAD"))
    files |= set(_git("ls-files", "--others", "--exclude-standard"))
    return sorted(f.replace("\\", "/") for f in files)


def select_checks(files: list[str]) -> tuple[list[Check], list[str]]:
    """Devuelve las comprobaciones necesarias y notas sobre lo que no cubren."""
    checks: list[Check] = [DOCTOR]  # AGENTS.md: cualquier cambio → doctor
    notes: list[str] = []
    if any(f.startswith(ROOT_TEST_PREFIXES) for f in files):
        checks.append(ROOT_TESTS)
    if any(f.startswith(ARCHIVE) for f in files):
        checks.append(ARCHIVE_TESTS)
        notes.append(
            "cambio en archive/: no forma parte del núcleo; la réplica Python archivada "
            "requiere pytest y sus dependencias y no se verifica aquí"
        )
    if any(f.startswith("projects/") and "/predictions/" in f for f in files):
        notes.append(
            "ledger de predicciones: solo se admiten líneas nuevas escritas con "
            "prediction_ledger.py; doctor verifica la cadena"
        )
    if any(f.endswith(".pine") for f in files):
        notes.append(
            "cambio en Pine: el comportamiento en TradingView no se verifica localmente; "
            "los MANIFEST.sha256 sí (doctor)"
        )
    return checks, notes


def run_check(check: Check) -> Result:
    if check.requires_module and importlib.util.find_spec(check.requires_module) is None:
        return Result(check.name, "skipped", f"falta el módulo '{check.requires_module}' (no verificado)")
    proc = subprocess.run(
        check.cmd, cwd=ROOT / check.cwd, capture_output=True, text=True, check=False,
        encoding="utf-8", errors="replace",
    )
    output = (proc.stdout + proc.stderr).strip().splitlines()
    if proc.returncode == 0:
        summary = next((ln for ln in reversed(output) if ln.strip()), "ok")
        if check is DOCTOR:
            summary = _doctor_summary(proc.stdout) or summary
        return Result(check.name, "pass", summary.strip())
    return Result(check.name, "fail", f"código {proc.returncode}", output[-25:])


def _doctor_summary(stdout: str) -> str:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return ""
    s = data.get("summary", {})
    warns = [c["check"] for c in data.get("checks", []) if c.get("status") == "warn"]
    text = f"pass={s.get('pass')} warn={s.get('warn')} fail={s.get('fail')}"
    return text + (f" (avisos: {', '.join(warns)})" if warns else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", help="rama o commit de referencia (p. ej. main)")
    ap.add_argument("--all", action="store_true", help="ejecutar todas las comprobaciones")
    ap.add_argument("--json", action="store_true", help="salida JSON")
    args = ap.parse_args(argv)

    try:
        files = [] if args.all else changed_files(args.base)
    except RuntimeError as exc:
        print(f"verify: {exc}", file=sys.stderr)
        return 2
    checks, notes = (list(ALL_CHECKS), []) if args.all else select_checks(files)
    results = [run_check(c) for c in checks]
    failed = [r for r in results if r.status == "fail"]

    if args.json:
        print(json.dumps({
            "ok": not failed,
            "changed_files": files,
            "results": [r.__dict__ for r in results],
            "not_verified": notes + [r.detail for r in results if r.status == "skipped"],
        }, ensure_ascii=False, indent=2))
        return 1 if failed else 0

    scope = "todo" if args.all else f"{len(files)} archivo(s) cambiado(s)"
    print(f"verify: {scope}")
    for r in results:
        mark = {"pass": "OK  ", "fail": "FAIL", "skipped": "SKIP"}[r.status]
        print(f"  {mark} {r.name}: {r.detail}")
        for line in r.tail:
            print(f"       {line}")
    for n in notes:
        print(f"  NO VERIFICADO: {n}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
