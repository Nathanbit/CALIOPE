# CALIOPE — contexto para Claude Code

@AGENTS.md

## Mapa rápido

| Ruta | Qué es |
|---|---|
| `prediction_ledger.py` | Registro de predicciones encadenado: `seal`, `ask`, `forecast`, `resolve`, `resolve-auto`, `verify`, `score`, `rival`, `ranges`, `state`, `learn`, `lesson-propose`, `lesson-decide`, `lesson-score` |
| `concepts.py` | Conceptos (idea LCM): predicción como estructura → texto, criterio y regla deterministas; clase de concepto para aprender |
| `forecast_scoring.py` | Puntuación de solo lectura: Brier/log, Murphy, `learn`, `score_lessons`, `rival` (bootstrap) y `thought_state` (estado continuo, idea Coconut) |
| `resolver.py` | Reglas de resolución y velas: evalúa sin ver pronósticos; `tools/fetch_klines.py` descarga datos públicos |
| `recursive_evidence.py` | Ledger encadenado por SHA-256 de operaciones y cartera |
| `caliope_loop.py` | Runner fail-closed: `init`, `ingest`, `decision`, `replay`, `status`, `doctor`, `scan-secrets` |
| `caliope_doctor.py` | `doctor` y escáner de secretos (los invoca `caliope_loop.py`) |
| `evidence_io.py` | Primitivas comunes: JSON canónico, lectura estricta, escritura atómica, bloqueo, fechas y manifiestos `sha256sum` |
| `contracts/`, `workflows/` | Esquemas JSON y contratos de revisión por proyecto |
| `desk/` | La mesa: agentes proponen órdenes desde pronósticos sellados; límites de `policy.json`, aprobación humana en terminal, ledger de órdenes; BloFin demo por defecto. Ver `desk/README.md` |
| `tools/verify.py` | Comprobaciones según lo que cambió |
| `evals/predict/`, `tools/eval_predict.py` | Casos fijos mensaje → concepto y su calificación determinista |
| `labs/causal_btc/` | Experimento aparte: causal-ts busca qué anticipa a BTC; hipótesis congeladas y validadas en holdout. No lo importa el núcleo |
| `labs/kronos_author/` | Kronos (modelo base de velas) como autor `ia:kronos`: caminos muestreados uno a uno y juzgados con el resolutor; propone, no sella |
| `labs/polymarket/` | Espejo exacto de los mercados "Bitcoin above X on <fecha>" como preguntas `close_beyond` 1m, autor `mercado:polymarket` |
| `tools/panel/` | Panel local: dictar o escribir una predicción → concepto → comandos de `seal`; vista del ledger. No escribe en el ledger |
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
