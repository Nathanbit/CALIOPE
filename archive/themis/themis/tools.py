"""Herramientas que pueden usar los agentes. Todas gratuitas por defecto."""
from __future__ import annotations

import html
import os
import re
from pathlib import Path

import httpx

UA = {"User-Agent": os.environ.get("THEMIS_UA", "Themis research bot contact@example.com")}


async def web_search(query: str, n: int = 6) -> str:
    """Búsqueda web. Usa Brave/Tavily si hay clave; si no, DuckDuckGo HTML (gratis)."""
    async with httpx.AsyncClient(timeout=30, headers=UA, follow_redirects=True) as c:
        if os.environ.get("TAVILY_API_KEY"):
            r = await c.post("https://api.tavily.com/search",
                             json={"api_key": os.environ["TAVILY_API_KEY"], "query": query, "max_results": n})
            return "\n".join(f"- {x['title']} | {x['url']}\n  {x['content'][:300]}" for x in r.json().get("results", []))
        if os.environ.get("BRAVE_API_KEY"):
            r = await c.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": n},
                            headers={"X-Subscription-Token": os.environ["BRAVE_API_KEY"]})
            res = r.json().get("web", {}).get("results", [])
            return "\n".join(f"- {x['title']} | {x['url']}\n  {x.get('description', '')[:300]}" for x in res)
        r = await c.post("https://html.duckduckgo.com/html/", data={"q": query})
        items = re.findall(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
                           r.text, flags=re.S)[:n]
        clean = lambda s: html.unescape(re.sub(r"<.*?>", "", s)).strip()
        return "\n".join(f"- {clean(t)} | {u}\n  {clean(s)[:300]}" for u, t, s in items) or "Sin resultados."


async def fetch_url(url: str, max_chars: int = 12000) -> str:
    """Descarga una página o PDF (texto) y devuelve texto plano truncado."""
    async with httpx.AsyncClient(timeout=45, headers=UA, follow_redirects=True) as c:
        r = await c.get(url)
    if "pdf" in r.headers.get("content-type", "") or url.lower().endswith(".pdf"):
        return _pdf_text(r.content)[:max_chars]
    text = re.sub(r"<(script|style).*?</\1>", " ", r.text, flags=re.S)
    text = html.unescape(re.sub(r"<.*?>", " ", text))
    return re.sub(r"\s+", " ", text)[:max_chars]


def _pdf_text(data: bytes) -> str:
    try:
        import io

        from pypdf import PdfReader
        return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
    except ImportError:
        return "[instala pypdf para leer PDFs]"


async def read_file(path: str, max_chars: int = 20000) -> str:
    """Lee un archivo local (txt, md, pdf, docx)."""
    p = Path(path)
    if not p.exists():
        return f"[no existe: {path}]"
    if p.suffix.lower() == ".pdf":
        return _pdf_text(p.read_bytes())[:max_chars]
    if p.suffix.lower() == ".docx":
        try:
            import docx
            return "\n".join(x.text for x in docx.Document(str(p)).paragraphs)[:max_chars]
        except ImportError:
            return "[instala python-docx para leer .docx]"
    return p.read_text(encoding="utf-8", errors="ignore")[:max_chars]


async def edgar_search(query: str, forms: str = "10-K,10-Q,8-K", n: int = 6) -> str:
    """Búsqueda de texto completo en SEC EDGAR (gratis, requiere User-Agent con contacto)."""
    async with httpx.AsyncClient(timeout=30, headers=UA) as c:
        r = await c.get("https://efts.sec.gov/LATEST/search-index", params={"q": query, "forms": forms})
        hits = r.json().get("hits", {}).get("hits", [])[:n]
    out = []
    for h in hits:
        s = h["_source"]
        adsh = h["_id"].split(":")[0]
        out.append(f"- {s.get('display_names', ['?'])[0]} | {s.get('form')} | {s.get('file_date')} | "
                   f"https://www.sec.gov/Archives/edgar/data/{s['ciks'][0].lstrip('0')}/{adsh.replace('-', '')}/")
    return "\n".join(out) or "Sin resultados."


async def edgar_facts(cik: str, concept: str = "Revenues") -> str:
    """Datos XBRL estandarizados de una empresa (ingresos, activos, deuda...)."""
    cik = cik.zfill(10)
    async with httpx.AsyncClient(timeout=30, headers=UA) as c:
        r = await c.get(f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{concept}.json")
    if r.status_code != 200:
        return f"[EDGAR HTTP {r.status_code}: prueba otro concepto, p. ej. RevenueFromContractWithCustomerExcludingAssessedTax]"
    units = next(iter(r.json().get("units", {}).values()), [])[-8:]
    return "\n".join(f"{u.get('end')}: {u.get('val')} ({u.get('form')})" for u in units)


async def calc(expression: str) -> str:
    """Calculadora segura para ratios financieros, intereses, plazos, etc."""
    if not re.fullmatch(r"[\d\s+\-*/().,%^e]+", expression):
        return "[expresión no permitida]"
    try:
        return str(eval(expression.replace("^", "**").replace(",", ""), {"__builtins__": {}}, {}))  # noqa: S307
    except Exception as e:
        return f"[error: {e}]"


TOOLS = {
    "web_search": (web_search, "web_search(query): busca en la web"),
    "fetch_url": (fetch_url, "fetch_url(url): lee una página o PDF"),
    "read_file": (read_file, "read_file(path): lee un archivo local del expediente"),
    "edgar_search": (edgar_search, "edgar_search(query, forms): texto completo en SEC EDGAR"),
    "edgar_facts": (edgar_facts, "edgar_facts(cik, concept): datos XBRL de una empresa"),
    "calc": (calc, "calc(expression): aritmética exacta"),
}
