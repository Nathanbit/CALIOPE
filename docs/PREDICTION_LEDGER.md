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
python prediction_ledger.py --ledger $L resolve --question q-... --outcome false --observed @projects/macroflow/predictions/data/observado.json

# 4. Integridad, puntuación y aprendizaje
python prediction_ledger.py --ledger $L verify
python prediction_ledger.py --ledger $L score
python prediction_ledger.py --ledger $L learn
```

`doctor` verifica la cadena de todos los `projects/*/predictions/*.jsonl` y
falla si un registro fue editado, reordenado o borrado.

## Sellar en un paso con conceptos (idea LCM)

Inspirado en el *Large Concept Model* de Meta FAIR
([arXiv 2412.08821](https://arxiv.org/abs/2412.08821)), que razona sobre
conceptos (frases enteras) en vez de palabras. CALIOPE no puede alojar ese
modelo (solo biblioteca estándar, sin pesos); toma la idea de arquitectura:

- Una predicción se escribe como **concepto** (`concepts.py`): activo,
  evento, niveles, ventana, precio de referencia observado y su fuente. El
  texto, el criterio y la regla se **generan** del concepto: dos formas de
  decir lo mismo dan la misma pregunta, y `verify` rechaza una pregunta cuyo
  texto no coincide con el generado.
- Cada concepto tiene una **clase** sin números exactos, por ejemplo
  `BTCUSDT|touch|at_or_below|d1-2%|h<=24h`. `score` muestra la tasa observada
  por clase y `learn --by concept` calibra a cada autor por clase, de modo que
  lo aprendido se transfiere entre preguntas del mismo tipo.
- Se rechaza lo que no es una predicción: precio de referencia que ya cumple
  el evento, o fuera de los niveles de `touch_first`.

```powershell
python prediction_ledger.py --ledger $L seal --concept '{"asset":"BTCUSDT","event":"touch","interval":"15m","start":"2026-10-23T12:15:00Z","horizon_end":"2026-10-24T12:15:00Z","reference_price":81270,"reference_source":"Binance 12:00Z","level":80000,"side":"at_or_below"}' `
  --author nathan --probability 0.3 --rationale "máximos descendentes" --dry-run
# si el texto generado es correcto, el mismo comando sin --dry-run
```

`seal` escribe pregunta y pronóstico **juntos o ninguno**. En Claude Code,
la skill `caliope-predict` convierte un mensaje como "sella: BTC toca 80.000
antes del viernes, 30%" en el concepto, enseña el dry run y sella solo con tu
confirmación.

## Estado continuo entre pronósticos (idea Coconut)

Inspirado en *Chain of Continuous Thought* de Meta FAIR
([arXiv 2412.06769](https://arxiv.org/abs/2412.06769)): en vez de razonar de
cero en palabras en cada paso, el modelo pasa un **estado continuo** al
siguiente. CALIOPE no entrena modelos; adopta la idea como cadena auditable:

- Cada pronóstico guarda el `thought_state` de su autor **justo antes** de
  pronosticar: su calibración contraída por tramo de probabilidad, su Brier,
  su historial y la tasa observada en la clase de concepto de la pregunta, las
  lecciones adoptadas en su ámbito y `parent`, el hash de su pronóstico
  anterior. Los pronósticos de cada autor forman así una cadena continua.
- `verify` recalcula ese estado desde los registros anteriores: si alguien
  declara un historial o una calibración que el ledger no mostraba, falla,
  aunque el hash del registro sea correcto.
- `state --author X [--question q-...]` enseña el estado antes de
  pronosticar; la skill `caliope-predict` lo muestra para que cada resultado
  informe la siguiente decisión. Los pronósticos retroactivos no llevan estado
  (el ledger posterior se filtraría en él).

## Resolución automática

Una pregunta puede llevar una **regla legible por máquina** (`--rule`). La
regla forma parte del id: no se puede cambiar después. Tras el plazo, el
resolutor evalúa la regla sobre las velas observadas **sin ver los
pronósticos**, y registra el hash de los datos usados.

```powershell
# Al preguntar: la regla en JSON en línea, o @ruta a un .json dentro de CALIOPE
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

`fetch_klines.py` descarga además las 24 horas anteriores al inicio
(`--lookback-hours`, por defecto 24). El resolutor las ignora para decidir el
resultado, pero `resolve-auto` las usa para guardar en la resolución el
**rival de volatilidad**: la probabilidad que daba la volatilidad sola, con
velas que cerraron antes de la ventana, sin acceso al resultado.

- `touch`: principio de reflexión de un paseo aleatorio sin deriva,
  `P = 2·(1 − Φ(d / (σ·√N)))`, donde `d` es la distancia logarítmica al nivel,
  `σ` la volatilidad por vela y `N` las velas de la ventana.
- `close_beyond`: la misma fórmula. Es una cota superior, porque el cierre se
  mira una vez por vela.
- `touch_first`: `ln(U/S) / ln(U/L)`, la ruina del jugador. Es condicional a
  que se toque alguno; los `void` no se puntúan.

La volatilidad es por vela, así que no se anualiza y no cae en la trampa de
los 252 días bursátiles en un mercado que cotiza todos los días. Si faltan
velas anteriores, la resolución guarda `available: false` con el motivo y no
inventa nada.

## Compuertas (fallo cerrado, código de salida 2)

Todas las reglas viven en una sola función (`check`) que usan los comandos que
escriben **y** `verify`, que las reproduce registro a registro. Un registro
fabricado a mano con un hash correcto también falla si rompe una regla. Las
escrituras se hacen bajo un bloqueo exclusivo (`ledger.jsonl.lock`) con
`fsync`, así que dos procesos a la vez no rompen la cadena.

Además de las de la tabla: pronóstico retroactivo solo con `sealed_evidence`
de formato válido (sha256 de 64 hex y commit de 7-40 hex) y nunca con fecha
futura; en preguntas con regla, ningún pronóstico después de `rule.start`;
una pregunta con regla solo se resuelve con `resolve-auto` (a mano, solo
`void` con motivo); solo cuenta el **primer** pronóstico de cada autor por
pregunta; NaN e infinito nunca se guardan; los argumentos JSON son en línea o
`@ruta` dentro de la carpeta CALIOPE.


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
- **Rival base con bootstrap** (`rival`): cada pronóstico se compara con lo
  que habría dicho la historia sola, es decir, la tasa con la que las
  preguntas de su misma clase de concepto (o todas, si no tiene clase)
  resolvieron `true` **antes** de sellarlo, contraída hacia 0,5. La ventaja
  media de Brier lleva un intervalo bootstrap del 95% con semilla fija. El
  veredicto es `beats_rival` o `worse_than_rival` solo si el intervalo
  excluye el cero y hay al menos 30 casos; si no, `undistinguished` o
  `insufficient`. Una racha de suerte no es habilidad.
- **Segundo rival, la volatilidad** (campo `volatility` de cada autor): en
  las preguntas resueltas con `resolve-auto` y velas previas, cada autor se
  compara también con lo que decía la volatilidad sola. Es el "efecto
  conocido más cercano" para predicciones de toques de precio. Ganarle a la
  historia es fácil; ganarle a la volatilidad demuestra que el análisis
  aporta algo.
- **Corrección por muchas comparaciones**: con `k` autores comparados a la
  vez (el enjambre cuenta), los veredictos usan el intervalo de familia al
  `1 − 0,05/k` (Bonferroni), y `ci95` se muestra solo como referencia. Diez
  pruebas al 95% dan en promedio un falso aprobado; así no.
- **Enjambre** (`~enjambre`, idea de MiroFish): en cada pregunta con al menos
  dos autores (`--min-authors`), la media en log-odds de sus primeros
  pronósticos válidos, recortados a [0.01, 0.99]. Es una derivación que no
  se escribe en el ledger, y `rival` la puntúa como a un autor más. Un nombre
  con `~` no es válido en el ledger, así que nadie puede sellar como el
  enjambre. Análisis completo en [MIROFISH_REA.md](MIROFISH_REA.md).
- `void` y `failure` se cuentan aparte; nunca se puntúan como aciertos.

## Autores

Cada autor tiene una identidad en minúsculas, y su cadena, su estado continuo
y su puntuación son propios:

| Autor | Ejemplo |
|---|---|
| Persona | `nathan` |
| Modelo de IA | `ia:gemini`, `ia:chatgpt`, `ia:claude` |
| Indicador | `indicador:macroflow` |
| Simulación multiagente | `sim:mirofish` |
| Experto externo | `experto:fulano` |

`" IA:Gemini "` se guarda como `ia:gemini`; un tipo desconocido o un nombre
con espacios se rechaza. Para que un indicador o una simulación compitan,
alguien tiene que reducir su salida a una probabilidad y sellarla **antes**
del resultado. El acuerdo entre varios autores no convierte nada en hecho.
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
- [666ghj/MiroFish](https://github.com/666ghj/MiroFish) (AGPL-3.0, no se
  copió nada): motor de predicción por enjambre de agentes. Su README no
  incluye cómo medir los aciertos; CALIOPE lo trata como un autor más
  (`sim:`) para medirlo.
- [bhoult/correlation](https://github.com/bhoult/correlation) (GPL-3.0, no se
  copió nada): 65 experimentos que no le ganan al azar después de costos. De
  ahí salen comparar contra el efecto conocido más cercano y corregir por
  muchas pruebas.
- [goldmansachs/gs-quant](https://github.com/goldmansachs/gs-quant)
  (Apache-2.0): la volatilidad realizada como medida. CALIOPE la mide por
  vela, en vez de anualizarla con 252 días.
- [microsoft/skill-recorder](https://github.com/microsoft/skill-recorder)
  (MIT): evaluaciones fijas y deterministas de la parte con modelo
  (`evals/predict/`).
- [midudev/runonweb.ai](https://github.com/midudev/runonweb.ai) (MIT):
  ajustes de Whisper en el navegador para el panel.
- [google-research/harness2](https://github.com/google-research/harness2)
  (Apache-2.0): memoria persistente de lecciones respaldadas por evidencia
  que guía el trabajo futuro y se juzga a sí misma.
