---
paths:
  - "**/*.py"
---
# Python en CALIOPE

Condensado de `rules/common` y `rules/python` de ECC (affaan-m/ECC, MIT); manda
`AGENTS.md` si algo choca.

- Raíz (`caliope_loop.py`, `recursive_evidence.py`, `tools/`, `.claude/hooks/`):
  solo biblioteca estándar, Python 3.11+. Pruebas con `unittest` en `tests/`.
- Lab (`labs/macroflow_v0_9_5_import/`): dependencias de su `pyproject.toml`;
  pruebas con `pytest` desde esa carpeta. No importes el lab desde la raíz.
- Anotaciones de tipo en funciones públicas; `@dataclass(frozen=True)` para
  registros de evidencia y resultados.
- Errores: captura excepciones concretas; nada de `except: pass`. Un dato que
  falta se reporta como desconocido o hace fallar la compuerta, nunca se
  rellena con un valor por defecto.
- Rutas que vienen de usuarios o archivos: resuelve y comprueba que quedan
  dentro del workspace; rechaza `..`.
- Subprocesos con lista de argumentos, sin `shell=True`. YAML con
  `yaml.safe_load`. Nada de `eval`/`exec`.
- Abre texto con `encoding="utf-8"` explícito (la persona trabaja en Windows).
- Arreglo primero en el dueño del formato; elimina el código que quede obsoleto.
- Cada cambio de comportamiento trae su prueba. Antes de terminar:
  `python tools/verify.py`.
