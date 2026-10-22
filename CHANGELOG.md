# Changelog

## 0.8.0 — 2026-10-21

- Capa para Claude Code adaptada de ECC (affaan-m/ECC, MIT), selectiva para no
  inflar el contexto: `CLAUDE.md` (importa `AGENTS.md`, mapa y economía de
  contexto), reglas por ruta en `.claude/rules/` (`python.md`, `pine.md`) y
  agentes revisores `caliope-reviewer`, `caliope-security-reviewer` y
  `caliope-fail-closed-hunter`. Se reescribieron para CALIOPE; no se copiaron.
- `.claude/hooks/guard.py` + `.claude/settings.json`: bloquean push forzado,
  `--no-verify`, lectura de credenciales por shell, commits sin
  `core.hooksPath=.githooks`, y edición de workflows de GitHub, de la allowlist
  de seguridad y de cualquier archivo congelado en un `MANIFEST.sha256`.
  Compilan los `.py` editados y exigen `doctor` al cerrar un turno con cambios.
  Permisos que niegan leer `.env`, claves y credenciales.
- `tools/verify.py` y skill `caliope-verify`: ejecutan solo las comprobaciones
  que exige la tabla de `AGENTS.md` según los archivos cambiados y declaran lo
  que no se verificó.
- Seguridad: la herramienta `calc` de themis usaba `eval`; una expresión como
  `9^9^9` bloqueaba el proceso. Ahora usa `themis/safe_calc.py` (AST con
  límites de exponente, tamaño y profundidad).
- Lab MacroFlow: `test_optimizer_run_via_api` estaba desfasado respecto a la
  cuarentena (el optimizador responde 403 por defecto y no genera `.pine`).
  Ahora hay una prueba del bloqueo por defecto y otra del flujo habilitado.
- `.gitignore`: `/state/` (revisiones de sesión con P&L personal).

## 0.7.0 — 2026-10-20

- Skills de proyecto en `.claude/skills/` (Claude Code las carga al abrir el repo):
  - `caliope-pre-trade-gate`: compuerta GO / NO_GO / REVIEW_REQUIRED antes de
    cada orden manual (riesgo, beneficio/riesgo, operaciones del día, racha de
    pérdidas, enfriamiento, ventanas de sesión y checklist de ruptura/retesteo).
  - `caliope-session-review`: revisión de la sesión desde el export de SimpleFX,
    incluida la detección de ganadoras protegidas cerradas a mano antes del TP.
  - `caliope-backtest-review`: lista de verificación de backtests de MacroFlow.
- `tools/discipline/`: scripts offline que usan esas skills; reutilizan el
  parser de SimpleFX de `audit/discretionary.py`.
- Cortacircuitos de cuenta (diario/semanal/mensual con hora de liberación y
  enfriamiento por racha), intención `WATCHLIST` → `NO_ACTIONABLE_ORDERS`, y
  capa de coach en la revisión de sesión (etiquetas de comportamiento con
  evidencia, veredicto escalonado y reglas propuestas con decisión humana).
- `docs/reverse-engineering/`: método de sala limpia con REA (inventario con
  procedencia) y pruebas de caja negra del comportamiento de las skills origen.
- Ideas adaptadas de tradermonty/claude-trading-skills y wshobson/agents (MIT),
  encontradas vía skill-issue.in. No se copió código ni se instalaron scripts
  de terceros.

## 0.6.0 — 2026-10-20

- Precondiciones de determinismo (adaptadas de DeepInfer): `seeded_best_effort`
  y `batch_invariant` exigen `seed`; `batch_invariant` exige además
  `model_version`. Las afirmaciones no reproducibles se rechazan al ingerir.
- `caliope_loop.py replay`: verifica una afirmación de determinismo con una
  segunda captura y guarda el veredicto en `runs/<run>/replays/` sin tocar la
  respuesta original ni la etapa del run. Consume presupuesto.
- `docs/DETERMINISM.md`: niveles de determinismo, uso de `replay` e ideas
  pendientes (gramáticas en contrato 1.2, Pass@k).

## 0.5.1 — 2026-10-20

- Endurecimiento frente a GhostAction (workflows de GitHub Actions maliciosos
  que roban secretos). `doctor` falla ante cualquier workflow no aprobado por
  hash en `security/allowed_workflows.sha256` y ante secretos o marcadores de
  la campaña en archivos versionados.
- `caliope_loop.py scan-secrets [--staged]`: detecta claves de nube, GitHub,
  GitLab, IA (Anthropic, OpenAI, OpenRouter, Google) y otras, sin imprimir
  nunca el valor. Hook `.githooks/pre-commit` (fail-closed) para usarlo antes
  de cada commit.
- `.gitignore` amplía patrones de credenciales; `SECURITY.md` añade la lista de
  controles de cuenta y el procedimiento ante un workflow desconocido.

## 0.5.0 — 2026-10-20

- `caliope_loop.py doctor`: diagnóstico de solo lectura (patrón de REA). Verifica
  Python, contratos JSON, que ningún workflow active publicación/trading/live
  automático y los `MANIFEST.sha256` de cada proyecto. Distingue diferencias solo
  de fin de línea (`warn`) de cambios reales de contenido (`fail`, código 2).
- `AGENTS.md`: reglas para agentes de código: autoridad, clasificación de la
  evidencia (`observation`/`derivation`/`inference`/`unknown`), propiedad de
  cada formato y verificación obligatoria por tipo de cambio.
- MacroFlow lab: `macroflow/audit/causality.py` y `scripts/causality_report.py`
  detectan lookahead (señal que cambia al borrar el futuro) y dependencia del
  calentamiento, con informe JSON y código de salida usable como compuerta.
  Ideas de `lookahead-analysis`/`recursive-analysis` de Freqtrade,
  reimplementadas sin copiar código GPL.
- MacroFlow lab: `RiskGuard` añade racha de pérdidas, enfriamiento tras cierre y
  bloqueo por grado/hora con los veredictos `negative` de `audit/tradelog.py`.
  Opt-in en la clase; el puente las activa con `MF_MAX_CONSECUTIVE_LOSSES=3` y
  `MF_COOLDOWN_MINUTES=10` por defecto. Los cierres siempre pasan.
- MacroFlow lab: `macroflow/audit/discretionary.py` y
  `scripts/discretionary_report.py` analizan el historial "closed orders" de
  SimpleFX como evidencia discrecional separada (`promotion_eligible=false`).

## 0.4.0 — 2026-10-20

- Contrato de observaciones 1.1: bloque obligatorio `generation` con proveedor,
  modelo y versión, método de captura, salida estructurada, hashes de contrato y
  prompt, temperatura, `top_p`, seed, límite de tokens, huella de sistema,
  determinismo y momento de captura. Lo no expuesto por el proveedor va en `null`.
- Cada run congela su contrato en `inputs/observation.schema.json` y lo pinea por
  hash en el manifest. Los paquetes de `requests/` incluyen el esquema y los
  `generation_requirements` (temperatura 0, seed derivado del `run_id`, 1 500
  tokens de salida por revisor).
- Compuertas nuevas en `ingest`, todas fail-closed: hashes de contrato y prompt,
  credenciales en metadatos, timestamp con zona, `batch_invariant` solo con motor
  local, y política del run (`reproducibility`, `require_structured_output`).
- Política inicial: `reproducibility: recorded`, sin salida estructurada
  obligatoria y sin mínimo de capturas no manuales para `human_review`.
- Las capturas `manual_copy` se aceptan y quedan registradas por agente.
- `claude_themis` (el orquestador) deja de ser un `agent_id` válido de
  observación. El contrato 1.0 queda congelado en
  `contracts/observation-1.0.schema.json` y los runs 1.0 se siguen validando con
  él, sin conversión.

## 0.3.0 — 2026-10-19

- Añade un ledger recursivo append-only para observaciones, hipótesis,
  candidatos, validaciones y decisiones humanas de promoción.
- Registra cartera, correlación, concentración, TP/SL, PnL, MFE/MAE, slippage y
  sensibilidad a gaps sin incluir identificadores de cuenta.
- Incorpora la captura sanitizada NQ1!/ES1!/RTY1! del 19 de octubre como caso
  abierto y no promocionable.
- Bloquea aprendizaje desde una sola muestra, casos incompletos y selección
  oportunista que oculte validaciones fallidas.
- Mantiene publicación automática y trading automático desactivados.

## 0.2.0 — 2026-10-19

- Completa la primera ronda de ocho revisores externos para MacroFlow v0.9.5.
- Registra una síntesis de Claude que conserva desacuerdos y prioriza hechos del
  repositorio sobre afirmaciones de modelos.
- Añade un índice verificable de respuestas con hashes canónicos y de archivo.
- Rechaza la promoción de v0.9.5 y mantiene el prototipo en cuarentena.
- Confirma que publicación, trading y autoaprobación permanecen desactivados.

## 0.1.0 — 2026-10-18

- Importa el motor original `themis/` sin renombrar sus módulos.
- Define CALIOPE como capa de orquestación, evidencia y gobierno.
- Añade el primer contrato de revisión reproducible para MacroFlow.
- Separa propuestas generativas, compuertas deterministas y autorización humana.
- Documenta límites para una futura integración de IA física.

