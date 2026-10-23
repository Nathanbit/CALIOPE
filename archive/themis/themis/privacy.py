"""Redacción reversible de datos sensibles antes de enviar texto a proveedores en la nube."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# El orden importa: los patrones más específicos van primero.
PATTERNS = {
    "EMAIL": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "NUM_PROCESO": r"\b\d{5}-\d{4}-\d{4,6}\b",   # nº de proceso judicial (EC)
    "SSN_US": r"\b\d{3}-\d{2}-\d{4}\b",
    "RUC_EC": r"\b\d{13}\b",          # RUC Ecuador
    "CEDULA_EC": r"\b\d{10}\b",       # Cédula Ecuador
    "IBAN": r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b",
    "TARJETA": r"\b\d{4}(?:[ -]\d{4}){3}\b",     # 16 dígitos agrupados de a 4
    "TELEFONO": r"\+\d[\d\s().-]{8,}\d|\b\d{3}[ .-]\d{3}[ .-]\d{4}\b",
}


@dataclass
class Redactor:
    mapping: dict[str, str] = field(default_factory=dict)
    names: list[str] = field(default_factory=list)   # partes, clientes y contrapartes a ocultar
    counts: dict[str, int] = field(default_factory=dict)

    def _tok(self, kind: str, value: str) -> str:
        for tok, val in self.mapping.items():
            if val == value:
                return tok
        tok = f"[{kind}_{len(self.mapping) + 1}]"
        self.mapping[tok] = value
        self.counts[kind] = self.counts.get(kind, 0) + 1
        return tok

    def redact(self, text: str) -> str:
        for name in sorted(self.names, key=len, reverse=True):
            text = re.sub(re.escape(name), lambda m: self._tok("PARTE", m.group(0)), text, flags=re.I)
        for kind, pat in PATTERNS.items():
            text = re.sub(pat, lambda m, k=kind: self._tok(k, m.group(0)), text)
        return text

    def restore(self, text: str) -> str:
        for tok, val in self.mapping.items():
            text = text.replace(tok, val)
        return text
