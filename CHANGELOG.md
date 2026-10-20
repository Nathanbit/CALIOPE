# Changelog

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

