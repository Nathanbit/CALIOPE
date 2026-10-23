# MiroFish desmontado con el método REA

Ingeniería inversa de [666ghj/MiroFish](https://github.com/666ghj/MiroFish)
para decidir qué le sirve a CALIOPE. Revisión del commit `7657031a`
(2 oct 2026). Licencia AGPL-3.0: **no se copió código**. Solo se tomaron
ideas, reimplementadas desde cero.

## Método

[REA](https://github.com/morluto/rea) (MIT) hace ingeniería inversa de
binarios y apps sin código fuente. Su propia skill dice que, cuando el código
fuente está completo, no se usan sus motores y se lee el repositorio con
herramientas normales. MiroFish publica su código, así que de REA se tomó la
disciplina y no las herramientas: cada afirmación lleva su evidencia
(`ruta:línea`), se separa lo observado de lo inferido y se dice qué no se
comprobó.

## Qué es MiroFish

| Tipo | Afirmación | Evidencia |
|---|---|---|
| observation | Flujo: documento semilla → grafo de conocimiento (Zep) → personajes generados por LLM → simulación social en dos plataformas (Twitter y Reddit, motor OASIS) → informe redactado por un agente ReACT | `README.md` "Workflow"; `backend/scripts/run_parallel_simulation.py:1-30` |
| observation | Necesita una clave de LLM y una clave de Zep Cloud | `README.md` "Required Environment Variables" |
| observation | El prompt del informe le dice al modelo que lo simulado *es* el futuro ("你正在观察的不是实验数据，而是未来的预演") | `backend/app/services/report_agent.py:552-567` |
| observation | El repositorio no contiene Brier, calibración, backtest ni probabilidades: ninguna predicción se compara con lo que pasó después | búsqueda de `brier\|calibrat\|backtest\|probabilit\|accuracy` en `backend/` sin resultados fuera de tests |
| observation | Las configuraciones y los personajes los genera un LLM con temperatura 0.3-0.7 y sin semilla, así que dos ejecuciones dan mundos distintos | `simulation_config_generator.py:452`, `oasis_profile_generator.py:581` |
| observation | Borra los `<tool_result>` que el modelo inventa antes de que llegue el resultado real de la herramienta, y lo prueba | `report_agent.py:1144-1174`; `tests/test_report_tool_result_sanitizer.py` |
| observation | Solo acepta herramientas de una lista cerrada | `report_agent.py:1120-1131` |
| inference | Su salida es un informe narrativo, no una predicción falsable: sin probabilidad, sin fecha de cierre y sin criterio de resolución | se deduce de las tres primeras filas |
| inference | El "enjambre" se presenta como más preciso que un solo modelo, pero el repositorio no mide si lo es | se deduce de la cuarta fila |
| unknown | Su tasa de acierto real | no hay datos publicados en el repositorio |

## Qué se adoptó en CALIOPE

| Idea de MiroFish | Cómo queda en CALIOPE | Dónde |
|---|---|---|
| Muchas inteligencias predicen juntas | **El enjambre medible**: en cada pregunta con al menos dos autores, `~enjambre` es la media en log-odds de sus primeros pronósticos válidos, recortados a [0.01, 0.99]. Se puntúa contra el mismo rival base que los demás, con intervalo bootstrap. Si no le gana a la historia, es ruido con nombre bonito | `forecast_scoring.swarm_pairs`, `rival --min-authors` |
| Una simulación como fuente de predicciones | Autor `sim:mirofish` (PR #22): compite en igualdad cuando alguien reduce su informe a un concepto sellado antes del resultado | `docs/PREDICTION_LEDGER.md` "Autores" |
| Borrar los resultados de herramienta inventados | Ya cubierto con más fuerza: el ledger recalcula el `thought_state` (nadie puede declarar un historial que no tiene) y el concepto exige un `reference_source` observado | `_check_forecast`, `concepts.validate_concept` |

`~enjambre` empieza con `~`, que no es válido como nombre de autor del
ledger: nunca puede confundirse con un autor real ni sellarse a mano. Es una
derivación: no se escribe en el ledger.

## Qué no se adoptó, y por qué

- **El motor de simulación** (OASIS, Zep, personajes generados por LLM):
  necesita servicios con claves y dependencias externas. CALIOPE es solo
  biblioteca estándar y no maneja credenciales (`AGENTS.md`).
- **Tratar lo simulado como futuro**: contradice la clasificación de
  evidencia de CALIOPE. Una simulación es una `inference`; su informe nunca
  pasa a `observation`.
- **Inyectar variables "desde el ojo de Dios"** (escenarios hipotéticos):
  queda pendiente. Necesitaría preguntas condicionales ("si X, ¿Y?") con su
  propia forma de resolverse, y hoy no existen.

## Cómo medir MiroFish con CALIOPE

1. Ejecuta MiroFish fuera de CALIOPE, con tus claves y en tu PC.
2. Del informe, saca una afirmación falsable con nivel, fecha y activo, por
   ejemplo "BTC toca 80.000 antes del viernes".
3. Dale una probabilidad. Para que no sea inventada, se puede ejecutar la
   misma simulación N veces y usar la fracción de ejecuciones que predicen el
   evento. Si no hay número, no se sella.
4. Séllala con la skill `caliope-predict` y autor `sim:mirofish` **antes**
   de que empiece la ventana.
5. Tras 30 resultados, `rival` dice si MiroFish le gana a la historia, y si
   el enjambre (tú, tus IAs, MacroFlow y MiroFish) le gana a cada uno por
   separado.

## Sin verificar

- No se ejecutó MiroFish: requiere claves de LLM y Zep. Todo lo anterior sale
  de leer el código y el README.
- El agregado en log-odds no se ha comparado con otros agregadores
  (mediana, media extremizada) con datos reales. Con pocos datos, elegir el
  "mejor" sería sobreajustar.
