# CALIOPE — contexto para Claude Code

@AGENTS.md

## Mapa rápido

| Ruta | Qué es |
|---|---|
| `prediction_ledger.py` | Registro de predicciones encadenado: `ask`, `forecast`, `resolve`, `resolve-auto`, `verify`, `score`, `learn`, `lesson-propose`, `lesson-decide`, `lesson-score` |
| `resolver.py` | Reglas de resolución y velas: evalúa sin ver pronósticos; `tools/fetch_klines.py` descarga datos públicos |
| `recursive_evidence.py` | Ledger encadenado por SHA-256 de operaciones y cartera |
| `caliope_loop.py` | Runner fail-closed: `init`, `ingest`, `decision`, `replay`, `status`, `doctor`, `scan-secrets` |
| `contracts/`, `workflows/` | Esquemas JSON y contratos de revisión por proyecto |
| `tools/verify.py` | Comprobaciones según lo que cambió |
| `projects/macroflow/` | Primer caso de uso: Pine y evidencia congelados en `MANIFEST.sha256`, predicciones en `predictions/` |
| `archive/` | Piezas retiradas (themis, réplica Python, disciplina); sin mantenimiento, ver `archive/README.md` |

## Comandos

```bash
python tools/verify.py              # antes de dar algo por terminado
python caliope_loop.py doctor       # diagnóstico de solo lectura
git config core.hooksPath .githooks # una vez por clon (lo exige el hook de commit)
```

## Economía de contexto

Adaptado del principio de ECC "optimiza la ventana de contexto, persiste el
resto":

- No cargues `projects/*/evidence/` ni los `.pine` completos salvo que la tarea
  sea sobre ellos; busca con Grep y lee rangos.
- Los PNG de evidencia no aportan a cambios de código.
- Los informes largos van a archivos (`state/`, `runs/`); en el chat, solo el
  resumen con procedencia y lo que no se verificó.
- Delegar revisiones a los agentes de `.claude/agents/` mantiene la sesión
  principal limpia y da una mirada que no escribió el código.

## Guardas automáticas (`.claude/settings.json`)

Los hooks de `.claude/hooks/guard.py` bloquean: push forzado, `--no-verify`,
leer credenciales por shell, commits sin `.githooks` activo, y editar
workflows de GitHub, la allowlist de seguridad, cualquier archivo congelado en
un `MANIFEST.sha256` o un ledger de predicciones (solo se añade con su CLI). Al terminar un turno con cambios, `doctor` debe pasar.
Si una guarda bloquea algo legítimo, explícalo a la persona; no la esquives.
