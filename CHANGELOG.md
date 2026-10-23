# Changelog

## 0.16.0 — 2026-10-23

Estado continuo entre pronósticos (idea de *Chain of Continuous Thought*,
Coconut, de Meta FAIR, sin el modelo neuronal).

- Cada pronóstico guarda `thought_state`: calibración contraída del autor por
  tramo, Brier, historial y tasa observada en la clase de concepto, lecciones
  adoptadas y `parent` (su pronóstico anterior). `verify` lo recalcula desde
  los registros previos y rechaza un estado inflado aunque el hash sea válido.
- Comando `state --author X [--question q-...]`; la skill `caliope-predict`
  lo muestra antes de sellar.
- Pronósticos retroactivos sin estado, para que el ledger posterior no se
  filtre. 6 tests nuevos.

## 0.15.0 — 2026-10-23

Paso 2: sellar una predicción en un paso, con conceptos (idea del Large
Concept Model de Meta FAIR, sin el modelo neuronal).

- `concepts.py`: una predicción se expresa como concepto (activo, evento,
  niveles, ventana, precio de referencia observado y su fuente). El texto, el
  criterio y la regla se generan de forma determinista; se rechazan conceptos
  que no son predicciones (el precio ya cumple el evento).
- `prediction_ledger.py seal`: concepto → pregunta + pronóstico, ambos o
  ninguno, bajo un solo bloqueo; `--dry-run` pasa todas las compuertas sin
  escribir. `verify` comprueba que el texto de una pregunta con concepto
  coincide con el generado y que su clase es la correcta.
- Clase de concepto (sin números exactos): `score` incluye `by_concept` y
  `learn --by concept` calibra por autor y clase.
- Skill `caliope-predict`: de "sella: BTC toca 80.000 antes del viernes, 30%"
  al concepto, con dry run y confirmación; precio de referencia siempre
  observado, nunca inventado.
- 10 tests nuevos.

## 0.14.0 — 2026-10-23

Refactors de mantenibilidad de la auditoría, sin cambiar comportamiento ni
hashes (la cabecera y la puntuación del ledger real salen idénticas).

- `evidence_io.py`: único dueño de JSON canónico, lectura estricta sin NaN,
  escritura atómica, bloqueo exclusivo, fechas con zona horaria y el formato
  `sha256sum`. Lo usan `caliope_loop.py`, `recursive_evidence.py`,
  `prediction_ledger.py`, `resolver.py`, `caliope_doctor.py` y la guarda.
- `caliope_doctor.py`: `doctor` y el escáner de secretos salen del runner
  (`caliope_loop.py` baja de 1.068 a 847 líneas); los comandos `doctor` y
  `scan-secrets` siguen igual.
- `forecast_scoring.py`: puntuación, Murphy, `learn` y `score_lessons` salen
  de `prediction_ledger.py`, que queda como dueño del formato y las reglas.
- La CLI del ledger usa un manejador por subcomando en lugar de una cadena
  de `if/elif`.
- 5 tests nuevos para `evidence_io`; 122 tests OK.

## 0.13.1 — 2026-10-23

Segunda parte de la auditoría Thermos + anti-slop.

- Escáner de secretos y `doctor` fallan cerrados: un archivo de más de 2 MB
  se reporta como `too_large_to_scan` en vez de saltarse; un `git show`
  fallido detiene el escaneo; sin git, `doctor` marca `fail` (antes `warn`).
- Runner: `ingest_decision` valida todo antes de escribir (ya no deja un
  `decision.json` huérfano si se supera el límite de candidatos); la ruta del
  contrato de observación se guarda relativa al expediente, así un run
  sobrevive a otro clon o sistema operativo.
- Guarda: bloquea `git push -uf`, `--no-verify` aunque el mensaje tenga `;`,
  `git -c core.hooksPath=...`, escrituras al ledger desde la shell y lectura
  de credenciales con `python -c`/`node -e`; tolera diferencias de mayúsculas
  en rutas de Windows. Documentada como heurística: la garantía está en el
  pre-commit, `doctor` y la cadena.
- anti-slop: sin banners decorativos en el código; README sin cifras que
  caducan ni la promesa "no se pueden manipular"; referencias a `audit/`,
  `tools/discipline/` y "Themis" actualizadas; la regla de dataclasses
  congeladas se reemplaza por la práctica real (dicts validados + hash).
- Tests: `encoding="utf-8"` explícito, reloj fijo en el ledger, sin import
  sin usar. 117 tests OK.

## 0.13.0 — 2026-10-23

Correcciones de la auditoría Thermos + anti-slop (dos revisores en contexto
limpio; cada ataque confirmado tiene su test en `tests/test_ledger_audit.py`).

- Críticos: pronóstico retroactivo con cualquier JSON como "sello" (ahora
  exige sha256 de 64 hex y commit de 7-40 hex, sin fechas futuras);
  pronósticos con la ventana de la regla ya abierta; U+2028/U+2029/U+0085 en
  un texto inutilizaban el ledger (ahora se separa solo por "\n").
- `verify` reproduce todas las compuertas con la misma función que los
  escritores: un registro fabricado con hash correcto ya no pasa.
- Bloqueo exclusivo y `fsync` al escribir; solo cuenta el primer pronóstico
  de cada autor por pregunta; NaN rechazado; preguntas con regla solo se
  resuelven con `resolve-auto` o `void`; lecciones solo dentro de su ámbito y
  `lesson-score` sin la evidencia de la lección en el "antes"; ids de pregunta
  con tiempos y números normalizados; booleanos no son probabilidades; el
  marcador muestra la tasa de fallos por autor.
- `resolver.py` 1.1: exige `fetched_at` posterior al cierre y velas cerradas
  antes de la descarga; rechaza velas duplicadas, OHLC incoherente, filas mal
  formadas, ventanas de más de 500.000 velas y símbolos fuera de A-Z/0-9.
- `tools/fetch_klines.py`: solo escribe en `projects/*/predictions/data/`, no
  sobrescribe, falla cerrado ante errores de red o respuestas inesperadas.
- Argumentos JSON: en línea o `@ruta` dentro de CALIOPE (antes leía cualquier
  ruta del disco). `.gitattributes`: `*.jsonl` con fin de línea LF.

## 0.12.0 — 2026-10-22

- `resolver.py`: reglas legibles por máquina (`touch_first`, `touch`,
  `close_beyond`) evaluadas sobre velas sin ver los pronósticos. Falla cerrado
  ante velas faltantes, símbolo o intervalo distintos, precios inválidos o
  reglas no alineadas; dos niveles en la misma vela dan `void`.
- `prediction_ledger.py`: `ask --rule` (la regla forma parte del id) y
  `resolve-auto --klines`, que registra el SHA-256 de los datos y la versión
  del resolutor.
- `tools/fetch_klines.py`: descarga velas públicas de Binance desde el PC de la
  persona (sin claves), paginadas.
- Verificación: 11 tests nuevos; `tools/verify.py --all` en verde.

## 0.11.1 — 2026-10-22

- `README.md` reescrito: qué es CALIOPE, sus cuatro piezas, cómo empezar,
  estado actual con sus límites, estructura y lo que falta.
- El detalle del runner de 8 IAs pasa, sin cambios, a `docs/RUNNER.md`.
- La skill `caliope-verify` deja de mencionar el laboratorio archivado.

## 0.11.0 — 2026-10-22

- Segundo nivel de recursividad en `prediction_ledger.py`, inspirado en el
  *improvement harness* de google-research/harness2 (Apache-2.0):
  - `lesson-propose`: lecciones que citan preguntas ya resueltas.
  - `lesson-decide`: solo una persona adopta, rechaza o retira, con motivo.
  - `forecast --lessons`: un pronóstico declara qué lecciones adoptadas sigue.
  - `lesson-score`: compara el Brier antes y después de adoptar cada lección
    (30 casos por lado); si empeora, propone retirarla sin retirarla.
- Nueva compuerta: `resolve` rechaza un `resolved_at` en el futuro.
- `learn` incluye las preguntas que respaldan cada tramo, listas para citar.
- `.claude/rules/python.md` deja de apuntar a `labs/`.
- Verificación: `tools/verify.py --all` en verde.

## 0.10.0 — 2026-10-22

- Integra en la nueva estructura la capa de gobierno para Claude Code del
  PR #10 (adaptada de ECC, MIT): `CLAUDE.md`, guardas en
  `.claude/hooks/guard.py`, revisores en `.claude/agents/`, reglas por tipo de
  archivo, skill `caliope-verify` y `tools/verify.py`.
- Adaptaciones: `verify.py` deja de apuntar a `labs/` y `themis/`; los cambios
  en `archive/` ejecutan sus tests archivados y avisan de lo que no cubren.
- La guarda impide editar a mano `projects/*/predictions/*.jsonl`: los
  ledgers solo crecen con `prediction_ledger.py`.
- La corrección de seguridad de `calc` (sin `eval`, límites contra `9^9^9`) se
  aplica a `archive/themis/` con su test en `archive/tests/`.
- `.gitignore`: `/state/`.
- Verificación: `python tools/verify.py --all` en verde; 81 tests del núcleo OK.

## 0.9.0 — 2026-10-22

- `prediction_ledger.py`: registro de predicciones binarias encadenado por
  SHA-256 (`ask`, `forecast`, `resolve`, `verify`, `score`, `learn`).
  Compuertas: id ligado al texto/plazo/fuente/criterio, sin pronósticos tras
  el resultado, sin resolución antes del plazo ni sin pronósticos, `void` con
  motivo, probabilidades inválidas como `failure`, retroactivos solo con
  evidencia de sellado.
- Puntuación Brier y log por autor, referencias moneda/tasa base,
  descomposición de Murphy con residuo y ECE, aviso con menos de 30 muestras.
- `learn`: aprendizaje recursivo de la propia calibración con contracción y
  muestra mínima; solo propone, nunca escribe ni aplica.
- `doctor` verifica la cadena de `projects/*/predictions/*.jsonl`.
- Primer caso: predicción BTC del 2026-10-21 (sellada en `17235a6`) resuelta
  como `void` y `false` con datos de Binance.
- Ideas adaptadas sin copiar código de hey-research-lab/brier (MIT),
  calibaudit (Apache-2.0) y la lista de Metaculus. Detalle en
  `docs/PREDICTION_LEDGER.md`.
- Verificación: 66 tests OK (11 nuevos), `doctor` sano (14 pass, 2 warn previos).

## 0.8.0 — 2026-10-22

- CALIOPE se centra en dos piezas: el registro de evidencia y predicciones, y
  el gobierno de modelos (proponen, verifican compuertas, decide una persona).
- Se mueven a `archive/`, sin borrar ni cambiar su contenido:
  - `themis/` → `archive/themis/` (motor heredado, sin integrar).
  - `labs/macroflow_v0_9_5_import/` → `archive/labs/` (réplica Python en
    cuarentena, optimizador, puente y broker MT5).
  - `tools/discipline/` y `tests/test_discipline.py` → `archive/tools/` y
    `archive/tests/`.
  - `.claude/skills/` → `archive/claude-skills/` (Claude Code ya no las carga).
  - `docs/reverse-engineering/` → `archive/docs/`.
- `README.md` y `AGENTS.md` describen el nuevo enfoque; MacroFlow queda como
  primer caso de uso en `projects/macroflow`.
- Verificación: `doctor` sano; 55 tests del núcleo OK. Los tests archivados de
  disciplina siguen pasando con `python -m unittest archive.tests.test_discipline`.

## 0.7.1 — 2026-10-21

- `labs/macroflow_v0_9_5_import/macroflow/risk/guards.py`: las compuertas de
  riesgo fallan cerradas ante entradas no válidas. Antes, un `equity` NaN en la
  primera consulta del día desactivaba el breaker de pérdida diaria todo el día,
  y un PnL NaN no contaba para la racha de pérdidas. Ahora:
  - `check_entry` devuelve `invalid_equity` si el equity no es finito o es ≤ 0,
    y nunca lo guarda como base del día;
  - un `day_start_equity` persistido corrupto bloquea (`invalid_day_start_equity`)
    en vez de reiniciar la base y la racha a mitad de día;
  - un PnL no finito cuenta como pérdida y queda marcado en el historial;
  - se rechazan datetimes naive, que `astimezone()` tomaba en silencio como hora
    local del servidor.
- `macroflow/timeutil.py`: `utc_now_iso()` sustituye a `datetime.utcnow()`
  (deprecado) con el mismo formato de salida.
- Tests: el del optimizador se divide en "bloqueado por defecto" (403) y
  "habilitado explícitamente"; la descarga de `.pine` se espera en 404 mientras
  la paridad Pine-Python siga pendiente. 52/52 en el lab.
- Revisión en cadena: DeepSeek (propuesta), Gemini (red-team), Qwen (arbitraje)
  y Gemini (red-team final, sin bloqueantes). Sus opiniones son inferencias; los
  hechos se verificaron ejecutando el código.

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

