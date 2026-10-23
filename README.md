# CALIOPE

**Un registro verificable de predicciones y decisiones asistidas por IA.**

Hoy cualquiera hace predicciones —una persona, un "experto", un modelo de IA,
un indicador de trading— y casi nadie las mide. CALIOPE las **sella antes del
resultado**, las **resuelve con datos observados** y **puntúa a cada autor**
con reglas que detectan las trampas habituales (ver sus límites en
[`docs/PREDICTION_LEDGER.md`](docs/PREDICTION_LEDGER.md)). Con el tiempo responde con números a
la pregunta: *¿a quién conviene creer, y cuánto?*

Además, **aprende de sí mismo**: convierte su historial en lecciones, una
persona decide cuáles adoptar, y CALIOPE comprueba después si esas lecciones
de verdad mejoraron los pronósticos.

> **Regla central.** Los modelos proponen. Las compuertas deterministas
> verifican. Una persona decide. CALIOPE nunca envía órdenes, publica ni
> maneja credenciales, y todo lo que no puede comprobar falla cerrado.

## Qué hace

### 1. Registro de predicciones (`prediction_ledger.py`)

| Paso | Qué ocurre | Qué impide |
|---|---|---|
| **Preguntar** | Pregunta binaria con fecha absoluta, fuente y criterio de resolución | Cambiar la pregunta después: si se reformula, es otra pregunta |
| **Pronosticar** | Cada autor sella su probabilidad en una cadena SHA-256 | Pronosticar con el resultado ya conocido, o editar, reordenar o borrar líneas |
| **Resolver** | Resultado `true`, `false` o `void` (con motivo), solo después del plazo y con su fuente. Con una regla de máquina, `resolve-auto` lo decide solo a partir de las velas | Resolver antes de tiempo, sin pronósticos, con fecha futura o con datos incompletos |
| **Puntuar** | Brier y log score por autor, frente a una moneda al aire y a la tasa base, con descomposición de Murphy | Llamar "habilidad" a algo que no supera al azar, o sacar conclusiones con menos de 30 casos (avisa) |

### 2. Aprendizaje recursivo, en dos niveles

1. **`learn`**: compara la confianza que declaró cada autor con lo que pasó.
   Con pocos datos casi no ajusta, y solo propone a partir de 30 casos por
   tramo.
1. **Lecciones** (`lesson-propose` → `lesson-decide` → `lesson-score`): una
   lección cita la evidencia que la respalda, **solo una persona la adopta**,
   y después se mide si los pronósticos que la siguieron mejoraron. Si
   empeoran, CALIOPE propone retirarla; no la retira solo. Una lección nunca
   se evalúa con los datos que la originaron.

### Conceptos y estado continuo

- **Conceptos** (idea del *Large Concept Model* de Meta FAIR): una predicción
  se sella como estructura con `seal`; el texto y la regla se generan, y el
  aprendizaje agrupa por clase de concepto.
- **Estado continuo** (idea de *Chain of Continuous Thought*, Coconut): cada
  pronóstico guarda el estado de su autor en ese momento y se enlaza al
  anterior; `verify` lo recalcula.

Ninguna de las dos aloja un modelo neuronal: CALIOPE adopta su arquitectura
con reglas deterministas y verificables.

### 3. Gobierno de modelos (`caliope_loop.py`)

Prepara paquetes de revisión para 8 IAs (Perplexity, DeepSeek, Qwen, Z.ai,
Kimi, Gemini, Meta, NVIDIA), ingiere solo respuestas JSON que cumplan el
contrato, controla presupuestos e iteraciones y bloquea cualquier promoción sin
decisión humana. El acuerdo entre IAs no convierte una opinión en hecho.
Detalle en [`docs/RUNNER.md`](docs/RUNNER.md).

### 4. Protecciones para agentes de código (`CLAUDE.md`, `.claude/`)

Bloquean el push forzado, `--no-verify`, la lectura de credenciales y la
edición de evidencia congelada o de ledgers. `tools/verify.py` ejecuta solo
las pruebas que tocan según lo que cambió y declara lo que no se verificó.

## Empezar

Python 3.11 o posterior, sin dependencias externas. Desde la carpeta `CALIOPE`:

```powershell
git config core.hooksPath .githooks          # una vez por clon
python tools/verify.py --all                 # todo en verde antes de usarlo

$L = "projects/macroflow/predictions/ledger.jsonl"

# Sellar una predicción en un paso (concepto → pregunta + pronóstico)
python prediction_ledger.py --ledger $L seal --concept '@projects/macroflow/predictions/concepto.json' `
  --author nathan --probability 0.30 --rationale "..." --dry-run   # revisar; luego sin --dry-run

# O por partes
python prediction_ledger.py --ledger $L ask `
  --text "BTCUSDT cierra una vela de 15m por debajo de 80.000 antes del 2026-10-29T00:00Z" `
  --horizon-end 2026-10-29T00:00:00Z `
  --source "BINANCE:BTCUSDT 15m klines" `
  --criteria "true si algún cierre de 15m en la ventana es < 80.000; si no, false"
python prediction_ledger.py --ledger $L forecast --question q-... --author nathan --probability 0.30 --rationale "..."

# Tras el plazo: resolver y ver quién acierta
python prediction_ledger.py --ledger $L resolve --question q-... --outcome false --observed @projects/macroflow/predictions/data/observado.json
python prediction_ledger.py --ledger $L score
python prediction_ledger.py --ledger $L learn
python prediction_ledger.py --ledger $L lesson-score
```

Guía completa: [`docs/PREDICTION_LEDGER.md`](docs/PREDICTION_LEDGER.md).

## Estado actual (2026-10-23)

| Pieza | Estado |
|---|---|
| Registro de predicciones y lecciones | Funciona, auditado (Thermos + anti-slop, 2026-10-23). **Solo 1 pronóstico puntuado**: aún no hay muestra para conclusiones |
| Primer caso real | Predicción BTC del 2026-10-21, sellada en el commit `17235a6`: una pregunta `void` (el mínimo quedó a 57 USD del umbral) y otra `false`, que coincidió con el 25% asignado |
| Runner de 8 IAs | Funciona, pero exige copiar y pegar en cada web; usado una vez (consolidación del 2026-10-19, decisión `reject`) |
| MacroFlow | Primer caso de uso. La v0.9.6 vive en TradingView; en 15m dio PF 1,26 con solo 4 planes, que **no demuestran rentabilidad** |

## Estructura

| Ruta | Qué es |
|---|---|
| `prediction_ledger.py` | Registro de predicciones, puntuación, `learn` y lecciones |
| `resolver.py` | Resolución automática con reglas de máquina sobre velas; `tools/fetch_klines.py` descarga datos públicos |
| `recursive_evidence.py` | Ledger de evidencia por operación y cartera (observación → hipótesis → candidato → validación → promoción humana) |
| `caliope_loop.py` | Runner de revisión por modelos |
| `caliope_doctor.py` | `doctor` y escáner de secretos |
| `concepts.py` | Conceptos: la predicción como estructura; texto y regla generados, clase para aprender |
| `forecast_scoring.py` | Puntuación, calibración, `learn` y juicio de lecciones (solo lectura) |
| `evidence_io.py` | Primitivas comunes de todos los ledgers |
| `contracts/`, `workflows/` | Esquemas JSON y contratos de revisión |
| `projects/macroflow/` | Primer caso: Pine y evidencia congelados en `MANIFEST.sha256`; predicciones en `predictions/` |
| `tools/verify.py` | Verificación según lo que cambió |
| `docs/` | Diseño del registro, del runner, determinismo y evidencia |
| `archive/` | Piezas retiradas sin borrar (motor themis, réplica Python, disciplina); ver `archive/README.md` |

## Lo que falta

1. **IAs y MacroFlow como autores**: que cada revisión del runner y cada señal del indicador queden como pronósticos puntuables.
2. **Un rival base automático**, calculado con volatilidad histórica, al que haya que ganar.
3. **Sellado de tiempo independiente** con OpenTimestamps (hoy, manual desde tu PC).
4. **Panel visual** de calibración y lecciones.
5. **Preguntas no binarias** (rangos, con CRPS o cuantiles).
6. **Adaptadores** para que el runner consulte las 8 IAs sin copiar y pegar.

## Verificar

```powershell
python tools/verify.py            # lo que cambió
python tools/verify.py --all      # todo
python caliope_loop.py doctor     # diagnóstico de solo lectura
```

`AGENTS.md` define qué comprobación exige cada tipo de cambio y cómo se
clasifica la evidencia (observación, derivación, inferencia, desconocido).

## Ideas tomadas de otros proyectos

Todas reimplementadas desde cero, sin copiar código:
[hey-research-lab/brier](https://github.com/hey-research-lab/brier) (MIT),
[calibaudit](https://github.com/OmAcharya-avtr/calibaudit) (Apache-2.0),
[google-research/harness2](https://github.com/google-research/harness2)
(Apache-2.0), la [lista de Metaculus](https://www.metaculus.com/help/question-checklist/),
[affaan-m/ECC](https://github.com/affaan-m/ECC) (MIT) y
[morluto/rea](https://github.com/morluto/rea) (MIT).
