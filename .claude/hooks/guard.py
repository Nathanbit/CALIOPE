"""Hooks de Claude Code para CALIOPE (fail-closed, solo biblioteca estándar).

Adaptado de la arquitectura de hooks de ECC (affaan-m/ECC, MIT), pero con las
reglas de este repositorio. Un único script con cuatro modos para que la lógica
y las pruebas vivan en un solo lugar:

    pre-bash   PreToolUse(Bash): bloquea push forzado, --no-verify, lectura de
               secretos por shell y commits sin el hook .githooks activo.
    pre-edit   PreToolUse(Edit|Write|MultiEdit|NotebookEdit): bloquea cambios en
               evidencia congelada, allowlists de seguridad, workflows de
               GitHub Actions y archivos de credenciales.
    post-edit  PostToolUse(Edit|Write|MultiEdit): compila los .py editados y
               corre `doctor` si se tocó un workflow, contrato o manifest.
    stop       Stop: si hay cambios sin commit, `doctor` debe pasar antes de
               terminar el turno.

Protocolo (Claude Code): recibe JSON por stdin; código 2 bloquea y envía stderr
a Claude; código 0 deja continuar. Ante un JSON ilegible en modos pre-*, bloquea.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

BLOCK = 2

PROTECTED_EDIT = [
    (re.compile(r"^\.github/workflows/"),
     "workflows de GitHub Actions: CALIOPE no usa CI y cualquier workflow nuevo debe "
     "aprobarse por hash a mano (SECURITY.md, GhostAction)"),
    (re.compile(r"^security/allowed_workflows\.sha256$"),
     "allowlist de workflows: solo la persona la modifica"),
    (re.compile(r"(^|/)MANIFEST\.sha256$"),
     "manifest de evidencia congelada: regenerarlo ocultaría un cambio real"),
    (re.compile(r"^projects/[^/]+/predictions/[^/]+\.jsonl$"),
     "ledger de predicciones encadenado: solo se añaden líneas con prediction_ledger.py; "
     "editarlo a mano rompe la cadena"),
    (re.compile(r"(^|/)\.env(\.|$)(?!example$)"), "archivo de entorno con posibles secretos"),
    (re.compile(r"(^|/)(secrets|credentials)/"), "carpeta de credenciales"),
    (re.compile(r"\.(pem|key|p12|pfx)$|(^|/)id_(rsa|ed25519)|service-account.*\.json$|credentials.*\.json$"),
     "archivo de credenciales"),
]

SECRET_READ = re.compile(
    r"(^|[\s'\"/=<])("
    r"\.env(?!\.example\b)(\.[\w.-]+)?"
    r"|(secrets|credentials)/[\w./-]*"
    r"|[\w.-]*\.(pem|key|p12|pfx)"
    r"|id_(rsa|ed25519)\w*"
    r"|\.npmrc|\.pypirc"
    r")(?=$|[\s'\";|&)>])"
)
READ_CMDS = re.compile(r"\b(cat|type|less|more|head|tail|bat|sed|awk|grep|rg|xxd|strings|Get-Content|gc|cp|scp|curl"
                       r"|python3?\s+-c|py\s+-c|node\s+-e|perl\s+-e|ruby\s+-e)\b")
LEDGER_PATH = re.compile(r"predictions/[^\s'\"]*\.jsonl\b")
WRITE_OPS = re.compile(r">|\btee\b|\bsed\b[^;&|]*\s-i|\bperl\b[^;&|]*\s-[a-z]*i|\btruncate\b|\brm\b|\bmv\b"
                       r"|\bcp\b|\bdd\b|\bopen\(|\bwrite|Set-Content|Add-Content|Out-File|Remove-Item")


def project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[2]


def rel_path(file_path: str, root: Path) -> str:
    p = Path(file_path)
    try:
        p = p.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        # Windows: the drive letter or folder case may differ between the
        # hook payload and CLAUDE_PROJECT_DIR.
        full, base = os.path.normcase(str(p.resolve())), os.path.normcase(str(root.resolve()))
        if full.startswith(base + os.sep):
            p = Path(str(p.resolve())[len(base) + 1:])
    return PurePosixPath(*p.parts).as_posix() if p.parts else file_path


def deny(msg: str) -> int:
    print(f"[CALIOPE] Bloqueado: {msg}", file=sys.stderr)
    return BLOCK


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(("git", *args), cwd=root, capture_output=True, text=True, check=False)


def run_doctor(root: Path) -> tuple[int, str]:
    proc = subprocess.run((sys.executable, "caliope_loop.py", "doctor"), cwd=root,
                          capture_output=True, text=True, check=False)
    try:
        data = json.loads(proc.stdout)
        fails = [f"{c['check']}: {c['detail']}" for c in data.get("checks", []) if c.get("status") == "fail"]
        detail = "; ".join(fails) or proc.stderr.strip()
    except (json.JSONDecodeError, KeyError, TypeError):
        detail = (proc.stdout + proc.stderr).strip()[-800:]
    return proc.returncode, detail


# Modos. Las reglas de Bash son heurísticas: frenan errores y atajos de un
# agente, no a alguien decidido a evadirlas. La garantía real está en
# .githooks/pre-commit, doctor y la verificación de la cadena del ledger.

def pre_bash(data: dict, root: Path) -> int:
    cmd = str(data.get("tool_input", {}).get("command", ""))
    git_cmd = re.search(r"\bgit\b", cmd) is not None
    if git_cmd and re.search(r"\bpush\b", cmd) and re.search(
            r"(\s--force(-with-lease)?\b|\s-[a-zA-Z]*f[a-zA-Z]*\b|\s\+\S)", cmd):
        return deny("push forzado. Reescribir historia en GitHub borra evidencia; pídeselo a la persona.")
    if git_cmd and ("--no-verify" in cmd or re.search(
            r"\b(commit|merge)\b.*\s-(?!-)[a-zA-Z]*n[a-zA-Z]*\b", cmd)):
        return deny("--no-verify salta el escaneo de secretos de .githooks/pre-commit.")
    if git_cmd and re.search(r"core\.hookspath", cmd, re.IGNORECASE) and not re.search(
            r"\bconfig\s+core\.hookspath\s+\.githooks\b", cmd, re.IGNORECASE):
        return deny("cambiar core.hooksPath desactiva el escaneo de secretos (SECURITY.md).")
    if LEDGER_PATH.search(cmd) and WRITE_OPS.search(cmd) and "prediction_ledger.py" not in cmd:
        return deny("escritura directa en un ledger de predicciones: solo se añade con "
                    "prediction_ledger.py (editarlo rompe la cadena).")
    if READ_CMDS.search(cmd) and SECRET_READ.search(cmd):
        return deny("comando que lee o copia un archivo de credenciales (AGENTS.md: nunca leas credenciales).")
    if re.search(r"\bgit\b[^;&|]*\bcommit\b", cmd):
        hooks_path = git(root, "config", "--get", "core.hooksPath").stdout.strip()
        if hooks_path.rstrip("/") != ".githooks":
            return deny("el hook anti-secretos no está activo en este clon. Ejecuta primero:\n"
                        "  git config core.hooksPath .githooks\n"
                        "y repite el commit (SECURITY.md).")
    return 0


def frozen_files(root: Path) -> set[str]:
    """Rutas (relativas a la raíz) que algún MANIFEST.sha256 congela."""
    repo = str(Path(__file__).resolve().parents[2])
    if repo not in sys.path:
        sys.path.insert(0, repo)
    import evidence_io  # único dueño del formato sha256sum (en el repo de la guarda)

    frozen: set[str] = set()
    for manifest in root.glob("projects/*/MANIFEST.sha256"):
        base = manifest.parent.relative_to(root).as_posix()
        for entry in evidence_io.parse_sha256_manifest(manifest):
            frozen.add(f"{base}/{entry.path}")
    return frozen


def pre_edit(data: dict, root: Path) -> int:
    ti = data.get("tool_input", {})
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    if not path:
        return 0
    rel = rel_path(path, root)
    for pattern, reason in PROTECTED_EDIT:
        if pattern.search(rel):
            return deny(f"{rel} — {reason}. Si de verdad hay que cambiarlo, lo hace la persona.")
    if rel in frozen_files(root):
        return deny(f"{rel} está congelado en su MANIFEST.sha256. Crea una versión nueva "
                    "(otro archivo) en vez de editarlo; actualizar el manifest lo decide la persona.")
    return 0


def post_edit(data: dict, root: Path) -> int:
    ti = data.get("tool_input", {})
    path = ti.get("file_path") or ""
    if not path:
        return 0
    rel = rel_path(path, root)
    if rel.endswith(".py") and Path(path).exists():
        try:  # compile() en memoria: no escribe __pycache__
            compile(Path(path).read_bytes(), path, "exec", dont_inherit=True)
        except (SyntaxError, ValueError) as exc:
            print(f"[CALIOPE] {rel} no compila: {exc}", file=sys.stderr)
            return BLOCK
    if re.match(r"^(workflows|contracts)/", rel):
        rc, detail = run_doctor(root)
        if rc != 0:
            print(f"[CALIOPE] doctor falla tras editar {rel}: {detail}", file=sys.stderr)
            return BLOCK
    return 0


def stop(data: dict, root: Path) -> int:
    if data.get("stop_hook_active"):
        return 0  # evita bucles: ya se avisó una vez en este turno
    status = git(root, "status", "--porcelain")
    if status.returncode != 0 or not status.stdout.strip():
        return 0
    rc, detail = run_doctor(root)
    if rc != 0:
        print("[CALIOPE] Hay cambios sin commit y `python caliope_loop.py doctor` falla: "
              f"{detail}\nCorrígelo o explica a la persona por qué queda pendiente.", file=sys.stderr)
        return BLOCK
    return 0


MODES = {"pre-bash": pre_bash, "pre-edit": pre_edit, "post-edit": post_edit, "stop": stop}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in MODES:
        print(f"uso: guard.py {{{'|'.join(MODES)}}}", file=sys.stderr)
        return BLOCK
    mode = argv[1]
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        if mode.startswith("pre-"):
            return deny("entrada del hook ilegible (fail-closed).")
        return 0
    return MODES[mode](data, project_dir())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
