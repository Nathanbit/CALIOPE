"""Paso C: escribe los parámetros aprobados en una COPIA versionada del .pine.

TradingView no permite subir scripts por API: el fichero generado se pega a
mano en el editor. Por eso nunca se sobrescribe el original, y el puente
rechaza alertas cuyo model_version no coincide con la versión aprobada.

Reconoce declaraciones del tipo:
    atrMult = input.float(2.0, "ATR Mult", minval=0.5)
    int corrLen = input.int(defval = 50, title = "Pearson")
y una constante de versión:
    MODEL_VERSION = "0.9.4"     (o  const string MODEL_VERSION = "0.9.4")
"""
from __future__ import annotations

import re
from pathlib import Path


def _fmt(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return f"{v:.6g}" if "." in f"{v:.6g}" or "e" in f"{v:.6g}" else f"{v:.6g}.0"
    return f'"{v}"'


def inject_params(source: str, values: dict[str, object], version: str) -> tuple[str, list[str]]:
    changes = []
    for var, val in values.items():
        pat = re.compile(
            rf"^(?P<pre>\s*(?:(?:var|varip|const)\s+)?(?:\w+\s+)?{re.escape(var)}\s*=\s*input(?:\.\w+)?\(\s*(?:defval\s*=\s*)?)"
            rf"(?P<val>[^,\)]+)", re.M)
        new, n = pat.subn(lambda m: m.group("pre") + _fmt(val), source, count=1)
        if n == 0:
            raise KeyError(f"No encuentro el input '{var}' en el .pine")
        old = pat.search(source).group("val").strip()
        if old != _fmt(val):
            changes.append(f"{var}: {old} -> {_fmt(val)}")
        source = new
    vpat = re.compile(r'(MODEL_VERSION\s*=\s*")([^"]*)(")')
    if vpat.search(source):
        source = vpat.sub(lambda m: m.group(1) + version + m.group(3), source, count=1)
        changes.append(f"MODEL_VERSION -> {version}")
    return source, changes


def write_versioned_pine(pine_path: str | Path, out_dir: str | Path, param_values: dict,
                         mapping: dict[str, str], version: str) -> tuple[Path, list[str]]:
    pine_path = Path(pine_path)
    values = {mapping[k]: v for k, v in param_values.items() if k in mapping}
    new_src, changes = inject_params(pine_path.read_text(), values, version)
    out = Path(out_dir) / f"{pine_path.stem.split('_v')[0]}_v{version}.pine"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(new_src)
    return out, changes
