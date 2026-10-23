# Archivo

Piezas retiradas del núcleo de CALIOPE el 2026-10-22. Se conservan intactas
por si se retoman, pero **no se mantienen**, no forman parte de `doctor` ni de
la suite principal y no deben usarse como garantías del sistema.

| Ruta | Qué era | Por qué se archivó |
|---|---|---|
| `themis/` | Motor multiagente original del ZIP (DAG, proveedores) | Nunca se integró con el runner |
| `labs/macroflow_v0_9_5_import/` | Réplica Python de MacroFlow, optimizador, puente FastAPI y broker MT5 | En cuarentena: sin paridad con el Pine y orientada a ejecución automática, que CALIOPE prohíbe |
| `tools/discipline/`, `tests/test_discipline.py`, `claude-skills/` | Compuerta previa a operar y revisión de sesión desde SimpleFX | Fuera del foco actual (evidencia y gobierno) |
| `docs/reverse-engineering/` | Ingeniería inversa de sala limpia de las skills de trading | Documenta las piezas anteriores |

Para restaurar una pieza: muévela con `git mv` a su ruta original y recupera
su verificación en `AGENTS.md` antes de usarla.
