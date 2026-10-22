# CALIOPE — contexto para Claude Code

@AGENTS.md

## Mapa rápido

| Ruta | Qué es |
|---|---|
| `caliope_loop.py` | Runner fail-closed: `init`, `ingest`, `decision`, `replay`, `status`, `doctor`, `scan-secrets` |
| `recursive_evidence.py` | Ledger encadenado por SHA-256 de operaciones y cartera |
| `contracts/`, `workflows/` | Esquemas JSON y contratos de revisión por proyecto |
| `tools/discipline/` | Compuerta pre-trade y revisión de sesión (SimpleFX) |
| `tools/verify.py` | Comprobaciones según lo que cambió |
| `themis/` | Motor multiagente importado; no ejecutar con datos sensibles (SECURITY.md) |
| `labs/macroflow_v0_9_5_import/` | Réplica Python en cuarentena; ver su `REVIEW_STATUS.md` |
| `projects/macroflow/` | Pine, evidencia y revisiones congeladas en `MANIFEST.sha256` |

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
workflows de GitHub, la allowlist de seguridad o cualquier archivo congelado en
un `MANIFEST.sha256`. Al terminar un turno con cambios, `doctor` debe pasar.
Si una guarda bloquea algo legítimo, explícalo a la persona; no la esquives.
