"""CLI de Themis."""
from __future__ import annotations

import argparse
import asyncio
import sys

from .audit import AuditLog
from .orchestrator import Orchestrator
from .router import Router


def main() -> None:
    ap = argparse.ArgumentParser(prog="themis", description="Orquestador multi-agente para derecho y finanzas")
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--domain", choices=["legal", "finance"], default="legal")
        p.add_argument("--sensitivity", choices=["public", "confidential"], default=None,
                       help="confidential = solo modelos locales o APIs que no entrenan con tus datos")
        p.add_argument("--mode", choices=["free_first", "balanced", "quality_first"], default=None)
        p.add_argument("--protect", nargs="*", default=[], help="nombres de partes/clientes a ocultar")
        p.add_argument("--no-redact", action="store_true")

    r = sub.add_parser("run", help="ejecuta una tarea completa")
    r.add_argument("task")
    r.add_argument("--files", nargs="*", default=[])
    common(r)

    c = sub.add_parser("consensus", help="compara varios modelos ante la misma pregunta")
    c.add_argument("question")
    c.add_argument("-n", type=int, default=3)
    common(c)

    sub.add_parser("models", help="lista proveedores configurados y modelos reales descubiertos")
    sub.add_parser("verify-log", help="verifica la integridad de la bitácora de auditoría")

    a = ap.parse_args()

    if a.cmd == "models":
        asyncio.run(_models(a.config)); return
    if a.cmd == "verify-log":
        print("Bitácora íntegra ✅" if AuditLog().verify() else "¡Bitácora alterada! ❌"); return

    o = Orchestrator(a.config, a.sensitivity, a.mode, a.protect, redact=not a.no_redact)
    if a.cmd == "run":
        print(asyncio.run(o.run(a.task, a.domain, a.files)))
    else:
        print(asyncio.run(o.consensus(a.question, a.n)))
    print(f"\n[costo estimado: ${o.router.spent:.4f}]", file=sys.stderr)


async def _models(config: str) -> None:
    router = Router(config)
    for p in router.providers.values():
        state = "✅ disponible" if p.available else "⚪ sin clave / local apagado"
        print(f"\n{p.name} [{p.privacy}] {state}")
        if p.available:
            try:
                for m in await p.discover():
                    print(f"   · {m}")
            except Exception as e:
                print(f"   (no se pudo listar: {e})")


if __name__ == "__main__":
    main()
