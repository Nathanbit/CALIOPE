"""Verificación MECÁNICA: no depende de lo que los agentes afirmen, se recalcula y se contrasta.

Idea tomada del enfoque "re-ejecutar las verificaciones en la puerta de salida" (kernel THEMIS para
agentes de código) y de la regla "toda autoridad citada debe provenir de la fase de investigación"
(Themis Legal AI), adaptadas a trabajo jurídico y financiero.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field

import httpx

URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
CASE_RE = re.compile(r"\b\d{5}-\d{4}-\d{4,6}\b")
ART_RE = re.compile(r"\b(?:art(?:ículo|\.)?s?)\s*\.?\s*(\d{1,4}(?:\.\d+)?)", re.I)
ARITH_RE = re.compile(r"(-?\d[\d.,]*)\s*([+\-*/×x÷])\s*(-?\d[\d.,]*)\s*=\s*(-?\d[\d.,]*)")


@dataclass
class Evidence:
    """Libro mayor de evidencia: todo lo que las herramientas devolvieron (con hash)."""
    items: list[dict] = field(default_factory=list)

    def add(self, agent: str, tool: str, args: dict | None, result: str) -> None:
        self.items.append({"agent": agent, "tool": tool, "args": args or {}, "result": result,
                           "sha256": hashlib.sha256(result.encode()).hexdigest()[:16]})

    @property
    def corpus(self) -> str:
        return "\n".join(f"{i['args']} {i['result']}" for i in self.items)


def _num(s: str) -> float | None:
    s = s.strip().rstrip(".,")
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", s):      # 1.234.567,89 (estilo ES)
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def check_arithmetic(text: str) -> list[dict]:
    issues = []
    for a, op, b, c in ARITH_RE.findall(text):
        x, y, z = _num(a), _num(b), _num(c)
        if None in (x, y, z):
            continue
        try:
            got = {"+": x + y, "-": x - y, "*": x * y, "×": x * y, "x": x * y,
                   "/": x / y if y else None, "÷": x / y if y else None}[op]
        except KeyError:
            continue
        if got is not None and abs(got - z) > max(0.01, abs(z) * 0.005):
            issues.append({"severity": "alta", "check": "aritmética",
                           "where": f"{a} {op} {b} = {c}", "problem": f"El resultado correcto es {got:,.4f}",
                           "fix": "Corregir cifra y todo lo que dependa de ella"})
    return issues


def check_provenance(final: str, evidence: Evidence, extra_sources: str = "") -> list[dict]:
    """Toda URL, nº de proceso y artículo citado debe aparecer en la evidencia recolectada."""
    corpus = (evidence.corpus + extra_sources).lower()
    issues = []
    for url in set(u.rstrip(".,;") for u in URL_RE.findall(final)):
        if url.lower() not in corpus:
            issues.append({"severity": "alta", "check": "procedencia", "where": url,
                           "problem": "URL citada que ninguna herramienta consultó (posible invención)",
                           "fix": "Eliminarla o buscarla y leerla realmente"})
    for case in set(CASE_RE.findall(final)):
        if case not in corpus:
            issues.append({"severity": "alta", "check": "procedencia", "where": case,
                           "problem": "Número de proceso/sentencia sin respaldo en la investigación",
                           "fix": "Verificar en fuente oficial o marcar NO VERIFICADO"})
    cited = set(ART_RE.findall(final))
    unseen = sorted(a for a in cited if not re.search(rf"art\w*\.?\s*{re.escape(a)}\b", corpus))
    if unseen:
        issues.append({"severity": "media", "check": "procedencia", "where": "artículos " + ", ".join(unseen[:12]),
                       "problem": "Artículos citados que no aparecen en ninguna fuente consultada",
                       "fix": "Confirmar en el texto oficial vigente o marcar NO VERIFICADO"})
    return issues


async def check_urls_live(final: str, limit: int = 12) -> list[dict]:
    urls = sorted(set(u.rstrip(".,;") for u in URL_RE.findall(final)))[:limit]
    issues: list[dict] = []

    async def probe(client: httpx.AsyncClient, u: str) -> None:
        try:
            r = await client.get(u)
            if r.status_code >= 400:
                issues.append({"severity": "media", "check": "enlace", "where": u,
                               "problem": f"HTTP {r.status_code}", "fix": "Reemplazar por fuente accesible"})
        except Exception as e:
            issues.append({"severity": "media", "check": "enlace", "where": u,
                           "problem": f"No accesible ({type(e).__name__})", "fix": "Revisar manualmente"})

    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                 headers={"User-Agent": "Themis verifier"}) as c:
        await asyncio.gather(*(probe(c, u) for u in urls))
    return issues


def check_numbers_traceable(final: str, evidence: Evidence, task: str, limit: int = 8) -> list[dict]:
    """Cifras grandes del informe que no están en la tarea ni en ninguna fuente ni salen de un cálculo."""
    corpus = evidence.corpus + task
    corpus_nums = {_num(n) for n in re.findall(r"\d[\d.,]{3,}", corpus)}
    out = []
    for n in re.findall(r"\b\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?\b|\b\d{5,}\b", final):
        v = _num(n)
        if v is not None and v not in corpus_nums and n not in out:
            out.append(n)
    if not out:
        return []
    return [{"severity": "media", "check": "cifras", "where": ", ".join(out[:limit]),
             "problem": "Cifras sin origen en las fuentes consultadas ni en la solicitud",
             "fix": "Mostrar el cálculo (herramienta calc) o la fuente; si no, marcar NO VERIFICADO"}]


async def mechanical_gate(final: str, evidence: Evidence, task: str, check_live: bool = True,
                          extra_sources: str = "") -> dict:
    issues = check_arithmetic(final) + check_provenance(final, evidence, extra_sources)
    issues += check_numbers_traceable(final, evidence, task)
    if check_live:
        issues += await check_urls_live(final)
    blocking = [i for i in issues if i["severity"] == "alta"]
    return {"passed": not blocking, "issues": issues, "evidence_items": len(evidence.items)}
