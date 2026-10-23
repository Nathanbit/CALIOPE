# Registro de predicciones

`prediction_ledger.py` es la pieza nº 1 de CALIOPE: un registro de
predicciones binarias **selladas antes del resultado**, resueltas con datos
observados y puntuadas con reglas de puntuación propias. Solo usa la
biblioteca estándar de Python.

## Uso

```powershell
$L = "projects/macroflow/predictions/ledger.jsonl"

# 1. Pregunta: fecha absoluta, fuente y criterio de resolución explícitos
python prediction_ledger.py --ledger $L ask `
  --text "BTCUSDT cierra una vela de 15m por debajo de 80.000 antes del 2026-10-29T00:00Z" `
  --horizon-end 2026-10-29T00:00:00Z `
  --source "BINANCE:BTCUSDT 15m klines" `
  --criteria "true si algún cierre de 15m en la ventana es < 80.000; si no, false"

# 2. Pronóstico de cada autor (persona, modelo, "experto" o MacroFlow)
python prediction_ledger.py --ledger $L forecast --question q-... --author claude --probability 0.30 --rationale "..."

# 3. Resolución, solo después del plazo y con su fuente
python prediction_ledger.py --ledger $L resolve --question q-... --outcome false --observed observed.json

# 4. Integridad, puntuación y aprendizaje
python prediction_ledger.py --ledger $L verify
python prediction_ledger.py --ledger $L score
python prediction_ledger.py --ledger $L learn
```

`doctor` verifica la cadena de todos los `projects/*/predictions/*.jsonl` y
falla si un registro fue editado, reordenado o borrado.

## Resolución automática

Una pregunta puede llevar una **regla legible por máquina** (`--rule`). La
regla forma parte del id: no se puede cambiar después. Tras el plazo, el
resolutor evalúa la regla sobre las velas observadas **sin ver los
pronósticos**, y registra el hash de los datos usados.

```powershell
# Al preguntar: la regla en JSON (o la ruta a un archivo .json)
python prediction_ledger.py --ledger $L ask --text "BTC toca 84.000 antes que 85.200" `
  --horizon-end 2026-10-22T00:15:00Z --source "BINANCE:BTCUSDT 1m" `
  --criteria "Primer nivel tocado por máximos/mínimos de 1m; ninguno = void" `
  --rule '{"kind":"touch_first","symbol":"BTCUSDT","interval":"1m","start":"2026-10-21T18:17:00Z","lower":84000,"upper":85200,"true_if":"lower_first"}'

# Tras el plazo, en tu PC (este entorno no llega a Binance):
python tools/fetch_klines.py --symbol BTCUSDT --interval 1m `
  --start 2026-10-21T18:17:00Z --end 2026-10-22T00:15:00Z `
  --out projects/macroflow/predictions/data/BTCUSDT_1m_20261021.json
python prediction_ledger.py --ledger $L resolve-auto --question q-... `
  --klines projects/macroflow/predictions/data/BTCUSDT_1m_20261021.json
```

| Regla | Resultado |
|---|---|
| `touch_first` (`lower`, `upper`, `true_if`) | Qué nivel tocan primero los mínimos/máximos. Ninguno → `void`. Ambos en la misma vela → `void` (el orden no se puede saber) |
| `touch` (`level`, `side`: `at_or_below`/`at_or_above`) | Si el precio toca el nivel en algún momento |
| `close_beyond` (`level`, `side`: `below`/`above`) | Si alguna vela cerrada cierra más allá del nivel. Una mecha no cuenta |

El resolutor **falla cerrado** si falta una sola vela de la ventana, si el
símbolo o el intervalo no coinciden, si hay precios inválidos o si la regla no
está alineada a las velas. Datos que faltan nunca se leen como "no pasó nada".
`fetch_klines.py` solo usa la API pública de Binance: sin claves ni cuenta.

## Compuertas (fallo cerrado, código de salida 2)

| Compuerta | Qué impide |
|---|---|
| Cadena SHA-256 entre registros | Editar, reordenar o borrar una línea sin que `verify` lo detecte |
| Id de pregunta = hash de texto, plazo, fuente y criterio | Reformular la pregunta después y conservar los pronósticos |
| Sin pronósticos tras la resolución | Pronosticar cuando ya se conoce el resultado |
| Sin resolución antes del plazo ni sin pronósticos | Cerrar antes de tiempo o resolver solo lo conveniente |
| `void` exige un motivo del criterio | Anular selectivamente las preguntas falladas |
| Probabilidad inválida = `failure` | Convertir una respuesta vaga en 0,5 o descartarla en silencio |
| `made_at` retroactivo exige `sealed_evidence` | Inventar un pronóstico "previo" sin un sellado verificable |
| Fechas sin zona horaria rechazadas | Interpretar la hora local del servidor en silencio |

## Puntuación

- **Brier** (0 perfecto, 0,25 moneda al aire, 1 seguro y equivocado) y
  **log score**, por autor.
- **Referencias tontas**: moneda (0,5) y tasa base de la pregunta, si se
  declaró. Un autor que no las supera no tiene habilidad demostrada.
- **Descomposición de Murphy**: Brier = fiabilidad − resolución +
  incertidumbre, con el residuo dentro de cada bin para poder comprobar la
  identidad, y el error de calibración esperado (ECE).
- `void` y `failure` se cuentan aparte; nunca se puntúan como aciertos.
- Con menos de 30 pronósticos puntuados, el informe lo advierte: la
  calibración todavía no significa nada.

## Aprendizaje recursivo

`learn` hace que CALIOPE aprenda de su propio historial. Para cada autor y
tramo de probabilidad compara la confianza declarada con la frecuencia
observada:

1. **Encoge hacia lo declarado**: con `n` resultados y `prior_strength`
   pseudo-observaciones, la frecuencia ajustada es
   `(aciertos + k·p_declarada) / (n + k)`. Con pocos datos casi no se mueve.
2. **Solo propone con muestra mínima**: un tramo con menos de `min_n`
   (por defecto 30) resultados responde "acumulando evidencia (n/30)".
3. **Nunca se aplica solo**: la salida es una inferencia marcada
   `observation_only`, `automatic_edit: false`, `requires_human_decision: true`.
   `learn` no escribe en el ledger.

Es el mismo patrón que el Outcome Governor de MacroFlow v0.9.6 (muestra
mínima, contracción hacia un valor neutral), aplicado a las predicciones.

## Lecciones: mejorar al que mejora

`learn` es el primer nivel de recursividad: compara confianza y frecuencia.
El segundo nivel, inspirado en el *improvement harness* de
[Harness²](https://github.com/google-research/harness2) (Apache-2.0), guarda
**lecciones** y las juzga por sus resultados:

```powershell
# 1. Proponer: la lección debe citar preguntas ya resueltas
python prediction_ledger.py --ledger $L lesson-propose `
  --text "En rupturas con volumen < 0,5x, bajar la confianza en la continuación" `
  --scope claude --evidence q-...,q-... --proposed-by learn

# 2. Decidir (persona): proposed -> adopt | reject; adopted -> retire
python prediction_ledger.py --ledger $L lesson-decide --lesson l-... --decision adopt --decided-by nathan --reason "..."

# 3. Aplicarla al pronosticar
python prediction_ledger.py --ledger $L forecast --question q-... --author claude --probability 0.4 --lessons l-...

# 4. Juzgarla
python prediction_ledger.py --ledger $L lesson-score
```

| Compuerta | Qué impide |
|---|---|
| Evidencia obligatoria y resuelta | Lecciones inventadas o basadas en preguntas abiertas |
| Solo una persona adopta, rechaza o retira, con motivo | Que el sistema cambie su propio criterio solo |
| Transiciones `proposed→adopt/reject`, `adopted→retire` | Readoptar en silencio una lección retirada |
| Un pronóstico solo cita lecciones adoptadas, y nunca retroactivo | Atribuir a una lección aciertos anteriores a ella |
| `lesson-score` compara antes y después de la adopción, con 30 casos por lado | Juzgar una lección con los mismos datos que la crearon o con muestras mínimas |

`lesson-score` dice `helps`, `neutral`, `hurts` o `insufficient`. Si una
lección empeora los pronósticos, **propone** retirarla; no la retira.

A diferencia de Harness², aquí nada se adopta sin decisión humana registrada
y cada lección se evalúa fuera de la muestra que la originó.

## Primer caso registrado

`projects/macroflow/predictions/ledger.jsonl` contiene la predicción del
2026-10-21, sellada a las 18:17Z en el commit `17235a6` (archivo
`evidence/LIVE_PREDICTION_BTCUSDT_2026-10-21.json`, SHA-256 `72faeed5…`):

| Pregunta | Pronóstico | Resultado observado |
|---|---|---|
| Toca 84.000 antes que 85.200 | 0,55 | `void`: no tocó ninguno (mínimo 84.057,59; máximo 84.876,01) |
| Cierre de 15m por debajo de 83.500 | 0,25 | `false`: cierre mínimo 84.093,42 (Brier 0,0625) |

El archivo sellado no se modifica: el resultado vive en el ledger.

## Límites conocidos

- **La fecha de un commit no es prueba de tiempo**: quien controla el
  repositorio puede reescribirla. El registro de *push* de GitHub es mejor
  evidencia; la prueba independiente es OpenTimestamps (anclado en Bitcoin,
  gratis y sin claves). El entorno de los agentes no llega a sus servidores,
  así que se sella desde tu equipo tras cada predicción nueva:

  ```powershell
  pip install opentimestamps-client
  ots stamp projects/macroflow/predictions/ledger.jsonl   # crea ledger.jsonl.ots
  git add projects/macroflow/predictions/ledger.jsonl.ots
  # unas horas después, cuando Bitcoin confirme:
  ots upgrade projects/macroflow/predictions/ledger.jsonl.ots
  ots verify  projects/macroflow/predictions/ledger.jsonl.ots
  ```

  Como el ledger solo crece, cada `.ots` prueba que todas las líneas
  anteriores existían en ese momento.
- La cadena prueba que el archivo no cambió desde un hash dado, no que el
  autor no lo reescribiera entero antes de publicarlo. Publicar el hash de
  cabecera fuera del repo cierra ese hueco.
- Solo preguntas binarias. Las de rango o numéricas requieren reglas como
  CRPS y quedan fuera de esta versión.

## Procedencia de las ideas

Ningún código se copió; las ideas se reimplementaron desde cero:

- [hey-research-lab/brier](https://github.com/hey-research-lab/brier) (MIT):
  ledger encadenado sellado antes de la respuesta, rechazo de pronósticos
  posteriores, no resolver sin pronósticos, fallos explícitos y referencias
  tontas.
- [calibaudit](https://github.com/OmAcharya-avtr/calibaudit) (Apache-2.0):
  descomposición de Murphy con residuo dentro del bin y advertencias de
  muestra pequeña. La identidad es de Murphy (1973).
- [Lista de comprobación de Metaculus](https://www.metaculus.com/help/question-checklist/):
  fechas absolutas, fuente y criterio de resolución explícitos.
- [google-research/harness2](https://github.com/google-research/harness2)
  (Apache-2.0): memoria persistente de lecciones respaldadas por evidencia
  que guía el trabajo futuro y se juzga a sí misma.
