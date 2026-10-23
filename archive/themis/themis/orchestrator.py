"""Orquestador: planifica (DAG), ejecuta en paralelo, reflexiona, replanifica, sintetiza y
pasa una compuerta de verificación mecánica antes de entregar."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from .agents import AGENTS, Agent, extract_json
from .audit import AuditLog
from .privacy import Redactor
from .router import Router
from .verify import Evidence, mechanical_gate


class Orchestrator:
    def __init__(self, config: str = "config.yaml", sensitivity: str | None = None,
                 mode: str | None = None, protected_names: list[str] | None = None,
                 redact: bool = True, out_dir: str = "runs"):
        self.router = Router(config)
        self.sensitivity = sensitivity or self.router.policy.get("default_sensitivity", "confidential")
        self.mode = mode
        self.out_dir = Path(out_dir)
        self.audit = AuditLog(self.out_dir / "audit.jsonl")
        self.evidence = Evidence()
        self.redactor = Redactor(names=protected_names or [])
        if not redact:
            self.redactor.redact = lambda t: t  # type: ignore[method-assign]
            self.redactor.restore = lambda t: t  # type: ignore[method-assign]

    def _agent(self, name: str) -> Agent:
        return Agent(AGENTS[name], self.router, self.audit, self.redactor, self.sensitivity, self.mode,
                     self.evidence)

    # ---------- ejecución del DAG ----------
    async def _execute(self, steps: list[dict], results: dict[str, str], file_note: str) -> None:
        pending = {s["id"]: s for s in steps}
        while pending:
            ready = [s for s in pending.values() if all(d in results for d in s.get("depends_on", []))]
            if not ready:                      # dependencias rotas o ciclo: ejecutar lo que quede
                ready = list(pending.values())
            outs = await asyncio.gather(*[
                self._agent(s["agent"]).run(
                    s["task"] + file_note,
                    "\n\n".join(f"## Resultado {d}\n{results[d]}" for d in s.get("depends_on", []) if d in results))
                for s in ready], return_exceptions=True)
            for s, o in zip(ready, outs):
                results[s["id"]] = f"[ERROR: {o}]" if isinstance(o, Exception) else o
                pending.pop(s["id"])

    def _signals_gap(self, steps: list[dict], results: dict[str, str]) -> list[dict]:
        """Validación de señales en la frontera de cada paso: ¿entregó algo utilizable?"""
        gaps = []
        for s in steps:
            out = results.get(s["id"], "")
            if out.startswith("[ERROR") or len(out.strip()) < 40:
                gaps.append({"agent": s["agent"], "task": f"Repetir y completar: {s['task']}"})
        return gaps

    @staticmethod
    def _findings(steps: list[dict], results: dict[str, str]) -> str:
        return "\n\n".join(
            f"### {s['id']} · {AGENTS[s['agent']].role}\n"
            f"_Señales: {', '.join(s.get('produces', [])) or 'n/d'} · Aceptación: {'; '.join(s.get('acceptance', [])) or 'n/d'}_\n"
            f"{results[s['id']]}" for s in steps)

    # ---------- flujo principal ----------
    async def run(self, task: str, domain: str = "legal", files: list[str] | None = None) -> str:
        t0 = time.time()
        pol = self.router.policy
        self.audit.write("run_start", task=task[:500], domain=domain, sensitivity=self.sensitivity,
                         mode=self.mode or pol.get("mode"))
        file_note = f"\nArchivos del expediente disponibles (usa read_file): {', '.join(files)}" if files else ""
        goal = f"[Dominio: {domain}] {task}{file_note}"

        # 1) Planificación (DAG con señales y criterios de aceptación)
        plan_raw = await self._agent("planner").run(goal)
        steps = (extract_json(plan_raw) or {}).get("steps") or [
            {"id": "s1", "agent": "legal_researcher" if domain == "legal" else "financial_analyst",
             "task": goal, "depends_on": []}]
        steps = [s for s in steps if s.get("agent") in AGENTS][:7]

        # 2) Ejecución en paralelo
        results: dict[str, str] = {}
        await self._execute(steps, results, file_note)

        # 3) Reflexión + replanificación: señales, cobertura y consistencia; cierra brechas
        verdict: dict = {}
        for rnd in range(pol.get("max_replan_rounds", 1) + 1):
            findings = self._findings(steps, results)
            review = await self._agent("verifier").run("Audita estos hallazgos y señala problemas y brechas.", findings)
            verdict = extract_json(review) or {"approved": False, "issues": [], "gaps": []}
            gaps = self._signals_gap(steps, results) + (verdict.get("gaps") or [])
            gaps = [g for g in gaps if g.get("agent") in AGENTS][:3]
            self.audit.write("reflection", round=rnd, approved=verdict.get("approved"), gaps=len(gaps))
            if not gaps or rnd >= pol.get("max_replan_rounds", 1):
                break
            new = [{"id": f"r{rnd}_{i}", "agent": g["agent"], "task": g["task"],
                    "depends_on": [s["id"] for s in steps][:4]} for i, g in enumerate(gaps)]
            await self._execute(new, results, file_note)
            steps += new

        findings = self._findings(steps, results)

        # 4) Síntesis
        synth_task = (f"Solicitud original: {goal}\n\nIntegra todo en el informe final. Aplica las correcciones del "
                      f"verificador y marca como NO VERIFICADO lo que siga sin sustento.")
        final = await self._agent("synthesizer").run(
            synth_task, f"HALLAZGOS:\n{findings}\n\nOBSERVACIONES DEL VERIFICADOR:\n{verdict}")

        # 5) Compuerta mecánica: se recalcula y contrasta, sin fiarse de lo que dijeron los agentes
        gate = await mechanical_gate(final, self.evidence, task, pol.get("check_links_live", True),
                                     extra_sources=" ".join(files or []))
        if not gate["passed"]:
            fixes = "\n".join(f"- [{i['severity']}] {i['where']}: {i['problem']} → {i['fix']}" for i in gate["issues"])
            final = await self._agent("synthesizer").run(
                synth_task + "\n\nLa verificación mecánica detectó estos problemas. Corrígelos: elimina lo no "
                "sustentado o márcalo NO VERIFICADO. No agregues fuentes nuevas.",
                f"HALLAZGOS:\n{findings}\n\nBORRADOR ANTERIOR:\n{final}\n\nPROBLEMAS:\n{fixes}")
            gate = await mechanical_gate(final, self.evidence, task, pol.get("check_links_live", True),
                                         extra_sources=" ".join(files or []))

        status = ("✅ Verificación mecánica superada" if gate["passed"]
                  else "⚠️ CON ADVERTENCIAS: la verificación mecánica aún detecta problemas (ver anexo)")
        if gate["issues"] and gate["passed"]:
            status += f" ({len(gate['issues'])} observaciones menores)"
        final = f"> **Estado de verificación:** {status}\n\n{final}"

        path = self._save(task, final, findings, verdict, gate)
        self.audit.write("run_end", seconds=round(time.time() - t0, 1), cost_usd=round(self.router.spent, 4),
                         report=str(path), verifier_approved=verdict.get("approved"),
                         gate_passed=gate["passed"], evidence_items=len(self.evidence.items))
        return final

    async def consensus(self, question: str, n: int = 3) -> str:
        """Pregunta a n modelos de proveedores distintos y un juez resalta acuerdos y contradicciones."""
        used: set[str] = set()
        needs = self.router.agent_cfg.get("legal_researcher", {}).get("needs", [])
        answers = []
        for _ in range(n):
            cands = [(p, m) for p, m in self.router.candidates(needs, self.sensitivity, self.mode, used)
                     if m.provider not in {u.split("/")[0] for u in used}]
            if not cands:
                break
            p, m = cands[0]
            used.add(m.key)
            r = await p.chat(m, "Responde con rigor jurídico/financiero y cita fuentes. Si no sabes, dilo.",
                             [{"role": "user", "content": self.redactor.redact(question)}])
            self.router.spent += r.cost_usd
            answers.append((m.key, self.redactor.restore(r.text)))
            self.audit.write("consensus_call", model=m.key, cost_usd=round(r.cost_usd, 5))
        joined = "\n\n".join(f"### Modelo {k}\n{a}" for k, a in answers)
        judge = await self._agent("verifier").run(
            "Compara las respuestas de varios modelos. Indica: puntos en que COINCIDEN, en que DIFIEREN, "
            "afirmaciones que solo aparecen en un modelo (posibles alucinaciones) y qué verificar. "
            "Responde en texto (no JSON).", joined)
        return f"{joined}\n\n---\n## Análisis de consenso\n{judge}"

    def _save(self, task: str, final: str, findings: str, verdict: dict, gate: dict) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"informe_{time.strftime('%Y%m%d_%H%M%S')}.md"
        ledger = "\n".join(f"- {e['agent']} · {e['tool']}({e['args']}) · sha256:{e['sha256']}"
                           for e in self.evidence.items) or "(sin herramientas usadas)"
        path.write_text(
            f"# Informe\n\n**Solicitud:** {task}\n\n{final}\n\n---\n## Anexo A: hallazgos por agente\n{findings}\n\n"
            f"## Anexo B: verificador (LLM)\n```json\n{verdict}\n```\n\n"
            f"## Anexo C: compuerta mecánica\n```json\n{gate}\n```\n\n"
            f"## Anexo D: libro de evidencia\n{ledger}\n\n_Costo estimado: ${self.router.spent:.4f}_\n",
            encoding="utf-8")
        return path
