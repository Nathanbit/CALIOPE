"""Read-only diagnostics and supply-chain scanning for CALIOPE.

``doctor`` checks contracts, workflows, frozen evidence manifests, prediction
ledgers and the secret scan. Any "fail" means unhealthy. ``scan_secrets``
backs the pre-commit hook and never prints a secret's value, only where it
is. Both are exposed through ``caliope_loop.py doctor`` and
``caliope_loop.py scan-secrets``.

Standard library only.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import evidence_io as eio

ROOT = Path(__file__).resolve().parent
CONTRACTS = ROOT / "contracts"


def sha256_file(path: Path) -> str:
    return eio.sha256_hex(path.read_bytes(), upper=False)


# doctor: diagnóstico de solo lectura (patrón "doctor" de REA). No modifica
# nada salvo comprobar que runs/ es escribible. Cualquier "fail" devuelve 2.
UNSAFE_FLAG = re.compile(r"^\s*(automatic_publish|automatic_trading|live_execution)\s*:\s*true\b",
                         re.IGNORECASE | re.MULTILINE)


def _eol_variants(data: bytes) -> dict[str, str]:
    lf = data.replace(b"\r\n", b"\n")
    return {k: eio.sha256_hex(v, upper=False) for k, v in
            (("raw", data), ("lf", lf), ("crlf", lf.replace(b"\n", b"\r\n")))}


def _check_manifest(manifest: Path) -> list[dict]:
    out = []
    base = manifest.parent
    for entry in eio.parse_sha256_manifest(manifest):
        name = f"manifest:{manifest.relative_to(ROOT).as_posix()}:{entry.line}"
        if entry.digest is None:
            out.append({"check": name, "status": "fail", "detail": "línea con formato inválido"})
            continue
        want, rel = entry.digest, entry.path
        target = (base / rel).resolve()
        if base.resolve() not in target.parents:
            out.append({"check": name, "status": "fail", "detail": f"ruta fuera del proyecto: {rel}"})
            continue
        if not target.is_file():
            out.append({"check": name, "status": "fail", "detail": f"falta {rel}"})
            continue
        hashes = _eol_variants(target.read_bytes())
        if hashes["raw"] == want:
            continue
        if want in (hashes["lf"], hashes["crlf"]):
            out.append({"check": name, "status": "warn",
                        "detail": f"{rel}: coincide solo tras normalizar finales de línea (git autocrlf)"})
        else:
            out.append({"check": name, "status": "fail",
                        "detail": f"{rel}: hash distinto del congelado ({want[:12]}… vs {hashes['raw'][:12]}…)"})
    return out


# Seguridad de la cadena de suministro (respuesta a GhostAction, oct-2026).
# CALIOPE no usa CI: cualquier workflow de GitHub Actions debe estar aprobado
# por hash. El escáner nunca imprime el valor de un secreto, solo dónde está.
SECRET_PATTERNS = {
    "aws_access_key": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "github_fine_grained_pat": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    "gitlab_token": re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    "anthropic_key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "openai_key": re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}"),
    "openrouter_key": re.compile(r"\bsk-or-v1-[A-Za-z0-9]{32,}"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "slack_token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    "sendgrid_key": re.compile(r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"),
    "npm_token": re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
    "pypi_token": re.compile(r"\bpypi-[A-Za-z0-9_-]{50,}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY"),
}
# Marcadores publicados de la campaña GhostAction. Se escriben partidos para que
# este archivo no se detecte a sí mismo.
IOC_MARKERS = tuple("".join(parts) for parts in (
    ("193.32.", "204.199"), ("my-git", "hub.com"), ("AKIA_CTX", "_START"),
    ("send-", "secrets"), ("Prepare Cache ", "Busting"),
))
ALLOWED_WORKFLOWS = Path("security") / "allowed_workflows.sha256"
MAX_SCAN_BYTES = 2_000_000


def _git_files(staged: bool) -> list[tuple[str, bytes | None]]:
    """(name, content) for tracked or staged files. Content is None when the
    file is too large to scan: callers must report it, never skip it."""
    cmd = (["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"] if staged
           else ["git", "ls-files", "-z"])
    names = subprocess.run(cmd, cwd=ROOT, capture_output=True, check=True).stdout.split(b"\0")
    out = []
    for raw in names:
        if not raw:
            continue
        name = raw.decode("utf-8", "replace")
        if staged:
            data = subprocess.run(["git", "show", f":{name}"], cwd=ROOT, capture_output=True,
                                  check=True).stdout
        else:
            path = ROOT / name
            if not path.is_file():
                continue  # deleted in the working tree; the staged scan covers commits
            data = path.read_bytes() if path.stat().st_size <= MAX_SCAN_BYTES else None
        out.append((name, None if data is None or len(data) > MAX_SCAN_BYTES else data))
    return out


def scan_text(name: str, data: bytes) -> list[dict]:
    """Devuelve hallazgos sin el valor del secreto (solo archivo, línea y tipo)."""
    if b"\0" in data[:8192]:
        return []  # binario
    findings = []
    for line_no, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
        for kind, pattern in SECRET_PATTERNS.items():
            if pattern.search(line):
                findings.append({"file": name, "line": line_no, "kind": kind})
        for marker in IOC_MARKERS:
            if marker in line and name != "caliope_doctor.py":
                findings.append({"file": name, "line": line_no, "kind": "ghostaction_ioc"})
    return findings


def scan_secrets(staged: bool = False) -> list[dict]:
    findings: list[dict] = []
    for name, data in _git_files(staged):
        if data is None:
            findings.append({"file": name, "line": 0, "kind": "too_large_to_scan"})
            continue
        findings.extend(scan_text(name, data))
    return findings


def _check_workflows() -> list[dict]:
    wf_dir = ROOT / ".github" / "workflows"
    files = sorted(p for p in wf_dir.glob("*") if p.is_file()) if wf_dir.is_dir() else []
    if not files:
        return [{"check": "ci_workflows", "status": "pass", "detail": "sin workflows de GitHub Actions"}]
    allowed: dict[str, str] = {}
    allow_file = ROOT / ALLOWED_WORKFLOWS
    if allow_file.is_file():
        for entry in eio.parse_sha256_manifest(allow_file):
            if entry.digest is not None:
                allowed[entry.path] = entry.digest
    out = []
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        digest = sha256_file(f)
        if allowed.get(rel) == digest:
            out.append({"check": f"ci_workflow:{rel}", "status": "pass", "detail": "aprobado por hash"})
        else:
            why = "hash distinto del aprobado" if rel in allowed else "no aprobado"
            out.append({"check": f"ci_workflow:{rel}", "status": "fail",
                        "detail": f"{why}: revisa quién lo añadió; CALIOPE no usa CI"})
    return out


def _check_prediction_ledgers() -> list[dict]:
    """A tampered, reordered or truncated prediction ledger fails closed."""
    out: list[dict] = []
    ledgers = sorted((ROOT / "projects").glob("*/predictions/*.jsonl"))
    if not ledgers:
        return out
    try:
        import prediction_ledger
    except ImportError as exc:
        return [{"check": "prediction_ledgers", "status": "fail", "detail": f"no importable: {exc}"[:200]}]
    for ledger in ledgers:
        rel = ledger.relative_to(ROOT).as_posix()
        try:
            records = prediction_ledger.load(ledger)
            prediction_ledger.verify(records)
            out.append({"check": f"ledger:{rel}", "status": "pass",
                        "detail": f"cadena íntegra ({len(records)} registros)"})
        except prediction_ledger.GateError as exc:
            out.append({"check": f"ledger:{rel}", "status": "fail", "detail": str(exc)[:200]})
    return out


def doctor() -> dict:
    checks: list[dict] = []
    ok_py = sys.version_info >= (3, 11)
    checks.append({"check": "python", "status": "pass" if ok_py else "fail",
                   "detail": sys.version.split()[0] + ("" if ok_py else " (<3.11)")})
    for schema in sorted(CONTRACTS.glob("*.json")):
        try:
            eio.read_json_object(schema, ValueError)
            checks.append({"check": f"contract:{schema.name}", "status": "pass", "detail": "JSON válido"})
        except (OSError, ValueError) as exc:
            checks.append({"check": f"contract:{schema.name}", "status": "fail", "detail": str(exc)[:200]})
    for wf in sorted((ROOT / "workflows").glob("*.y*ml")):
        hits = UNSAFE_FLAG.findall(wf.read_text(encoding="utf-8"))
        checks.append({"check": f"workflow:{wf.name}", "status": "fail" if hits else "pass",
                       "detail": f"flags activados: {sorted(set(hits))}" if hits else "sin publicación/trading automático"})
    manifests = sorted((ROOT / "projects").glob("*/MANIFEST.sha256"))
    for manifest in manifests:
        found = _check_manifest(manifest)
        checks.extend(found or [{"check": f"manifest:{manifest.relative_to(ROOT).as_posix()}",
                                 "status": "pass", "detail": "todos los hashes coinciden"}])
    checks.extend(_check_prediction_ledgers())
    checks.extend(_check_workflows())
    try:
        found = scan_secrets()
        checks.append({"check": "secret_scan", "status": "fail" if found else "pass",
                       "detail": f"{len(found)} hallazgo(s): " + ", ".join(
                           f"{f['file']}:{f['line']} ({f['kind']})" for f in found[:10])
                       if found else "sin secretos ni marcadores de GhostAction en archivos versionados"})
    except (OSError, subprocess.CalledProcessError) as exc:
        # Sin git no se puede afirmar que esté limpio: falla cerrado.
        checks.append({"check": "secret_scan", "status": "fail", "detail": f"no ejecutado: {exc}"[:200]})
    try:
        runs = ROOT / "runs"
        runs.mkdir(exist_ok=True)
        ok_runs = os.access(runs, os.W_OK)
    except OSError:
        ok_runs = False
    checks.append({"check": "runs_dir", "status": "pass" if ok_runs else "fail", "detail": "runs"})
    summary = {k: sum(c["status"] == k for c in checks) for k in ("pass", "warn", "fail")}
    return {"healthy": summary["fail"] == 0, "summary": summary, "checks": checks}

