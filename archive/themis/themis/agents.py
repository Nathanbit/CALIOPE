"""Agentes especializados en derecho e investigación financiera."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .tools import TOOLS

RULES = """REGLAS INNEGOCIABLES
1. Nunca inventes leyes, artículos, sentencias, cifras ni citas. Si no lo puedes sustentar con una fuente consultada, escribe "NO VERIFICADO".
2. Cada afirmación jurídica o cifra importante lleva su fuente [Fuente: título/URL/artículo/documento y fecha].
3. Distingue siempre: hecho acreditado / inferencia / opinión / hipótesis.
4. Indica jurisdicción y vigencia de cada norma; advierte si puede haber sido reformada o derogada.
5. Marca incertidumbres y datos faltantes; no rellenes huecos con suposiciones.
6. Responde en el idioma del usuario."""

TOOL_PROTOCOL = """HERRAMIENTAS (opcionales). Para usar una, responde SOLO con JSON:
{"tool": "<nombre>", "args": {...}}
Herramientas disponibles:
{tools}
Cuando tengas suficiente información, responde con el entregable final en texto (sin JSON)."""


@dataclass
class AgentSpec:
    name: str
    role: str
    prompt: str
    tools: list[str] = field(default_factory=list)
    json_mode: bool = False


AGENTS: dict[str, AgentSpec] = {a.name: a for a in [
    AgentSpec("planner", "Planificador",
              "Eres el director de un despacho/mesa de investigación. Descompón la tarea en pasos y "
              "asigna cada uno al agente adecuado. Responde SOLO con JSON: "
              '{"steps":[{"id":"s1","agent":"<agente>","task":"...","depends_on":[],'
              '"produces":["<señal>"],"acceptance":["<criterio verificable>"]}]}. '
              "Agentes: fact_extractor, legal_researcher, adversarial_researcher, contract_analyst, "
              "financial_analyst, forensic_reviewer, drafter. "
              "En tareas jurídicas incluye SIEMPRE fact_extractor primero y adversarial_researcher en "
              "paralelo con legal_researcher. Máximo 7 pasos; paraleliza lo independiente. "
              "'produces' son las señales (hechos, cuestiones, autoridades, cifras) que el paso entrega; "
              "'acceptance' son criterios que se puedan comprobar.", json_mode=True),
    AgentSpec("fact_extractor", "Extractor de hechos",
              "Haces el intake: cronología, partes, hechos relevantes, daños/cuantías y VACÍOS PROBATORIOS "
              "(qué hechos faltan para decidir). Los cálculos se hacen con calc, nunca de cabeza. "
              "Entrega: registro fáctico numerado, cronología, cifras con su cálculo y lista de vacíos.",
              ["read_file", "calc"]),
    AgentSpec("adversarial_researcher", "Investigador adversarial",
              "Tu trabajo es buscar activamente lo que PERJUDICA la posición del cliente: autoridad contraria, "
              "excepciones, plazos vencidos, defensas de la contraparte y debilidades probatorias. "
              "Evita el sesgo de confirmación: reporta también cuando no encuentres autoridad contraria "
              "y qué buscaste.",
              ["web_search", "fetch_url", "read_file"]),
    AgentSpec("legal_researcher", "Investigador jurídico",
              "Investigas normativa, jurisprudencia y doctrina. Estructura: marco normativo aplicable, "
              "jurisprudencia relevante, análisis, riesgos y vacíos. Prioriza fuentes oficiales "
              "(registros oficiales, cortes, legislación consolidada).",
              ["web_search", "fetch_url", "read_file"]),
    AgentSpec("contract_analyst", "Analista de contratos",
              "Revisas contratos y documentos: obligaciones, plazos, penalidades, cláusulas de terminación, "
              "indemnización, ley aplicable, jurisdicción, riesgos y desequilibrios. Cita la cláusula exacta "
              "(número y extracto breve). Propone redacción alternativa.",
              ["read_file", "web_search"]),
    AgentSpec("financial_analyst", "Analista financiero",
              "Analizas estados financieros, ratios, flujos, valoración y riesgo. Muestra fórmulas y cálculos "
              "(usa calc, no cálculo mental). Contrasta con filings oficiales y señala supuestos.",
              ["edgar_search", "edgar_facts", "web_search", "fetch_url", "read_file", "calc"]),
    AgentSpec("forensic_reviewer", "Revisor forense",
              "Buscas anomalías: inconsistencias contables, partes relacionadas, transacciones atípicas, "
              "señales de manipulación (Benford, accruals, cambios de política contable). Separa indicios "
              "de conclusiones; nunca acuses sin evidencia.",
              ["read_file", "calc", "edgar_facts", "web_search"]),
    AgentSpec("drafter", "Redactor",
              "Redactas escritos, memorandos, dictámenes y resúmenes ejecutivos con estilo profesional "
              "a partir de los hallazgos. No agregues hechos o citas que no estén en los hallazgos."),
    AgentSpec("verifier", "Verificador (abogado del diablo)",
              "Auditas el borrador: detecta citas no sustentadas, normas posiblemente derogadas, saltos "
              "lógicos, cifras que no cuadran y contradicciones entre agentes. Revisa además: (a) COBERTURA: "
              "¿cada cuestión identificada fue investigada y tratada? (b) CONSISTENCIA: ¿los hechos del análisis "
              "coinciden con el registro fáctico? (c) SEÑALES: ¿cada paso entregó lo que prometía? "
              "Responde SOLO con JSON: "
              '{"approved":bool,"issues":[{"severity":"alta|media|baja","where":"...","problem":"...","fix":"..."}],'
              '"gaps":[{"agent":"<agente>","task":"<tarea concreta para cerrar la brecha>"}]}',
              json_mode=True),
    AgentSpec("synthesizer", "Sintetizador",
              "Integras los hallazgos y las correcciones del verificador en un informe final: "
              "1) Resumen ejecutivo 2) Hallazgos 3) Análisis 4) Riesgos y puntos NO VERIFICADOS "
              "5) Próximos pasos 6) Fuentes. REGLA: toda autoridad, URL, cifra o artículo debe provenir de los "
              "hallazgos; no agregues ninguna nueva al redactar. Termina con: 'Este documento es apoyo de investigación y "
              "no sustituye el criterio de un profesional habilitado.'"),
]}


def extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


class Agent:
    def __init__(self, spec: AgentSpec, router, audit, redactor, sensitivity: str, mode: str | None,
                 evidence=None):
        self.spec, self.router, self.audit = spec, router, audit
        self.evidence = evidence
        self.redactor, self.sensitivity, self.mode = redactor, sensitivity, mode

    async def run(self, task: str, context: str = "", max_steps: int = 6) -> str:
        sp = self.spec
        system = f"{sp.prompt}\n\n{RULES}"
        if sp.tools:
            listing = "\n".join(TOOLS[t][1] for t in sp.tools)
            system += "\n\n" + TOOL_PROTOCOL.replace("{tools}", listing)
        user = f"CONTEXTO:\n{context}\n\nTAREA:\n{task}" if context else task
        messages = [{"role": "user", "content": self.redactor.redact(user)}]

        for step in range(max_steps):
            resp = await self.router.complete(
                sp.name, system, messages, sensitivity=self.sensitivity, mode=self.mode,
                json_mode=sp.json_mode)
            text = self.redactor.restore(resp.text)
            self.audit.write("llm_call", agent=sp.name, model=resp.model, step=step,
                             tokens_in=resp.tokens_in, tokens_out=resp.tokens_out,
                             cost_usd=round(resp.cost_usd, 5), redactions=dict(self.redactor.counts))
            call = extract_json(resp.text) if sp.tools else None
            if not (call and call.get("tool") in sp.tools):
                return text
            fn = TOOLS[call["tool"]][0]
            try:
                result = await fn(**call.get("args", {}))
            except Exception as e:
                result = f"[error de herramienta: {e}]"
            self.audit.write("tool_call", agent=sp.name, tool=call["tool"], args=call.get("args"))
            if self.evidence is not None:
                self.evidence.add(sp.name, call["tool"], call.get("args"), str(result))
            messages += [{"role": "assistant", "content": resp.text},
                         {"role": "user", "content": self.redactor.redact(f"RESULTADO de {call['tool']}:\n{result}")}]
        return text + "\n\n[Aviso: se alcanzó el máximo de pasos de herramientas]"
