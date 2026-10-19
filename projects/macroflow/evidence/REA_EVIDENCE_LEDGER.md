# MacroFlow REA — Evidence Ledger base v0.4 + ampliación v0.5

## Control del registro

| Campo | Valor |
|---|---|
| Fecha de corte | 2026-10-13 |
| REA CLI | 6.4.0 |
| Proyecto | MacroFlow REA — base v0.4 y experimento v0.5 |
| Objetivo público observado | https://es.tradingview.com/script/lzNtISvY/ |
| Estado del objetivo | Script público de TradingView con código Pine protegido |
| Alcance de este registro | Procedencia de evidencia, hipótesis propias y protocolo de validación |

Este documento no afirma conocer el código fuente ni las reglas internas exactas
del objetivo público. El Pine del objetivo está protegido. No se extrajo, copió,
descompiló ni reconstruyó su lógica privada. La implementación MacroFlow es una
obra propia basada en fuentes públicas, comportamiento visible, literatura de
mercados y reglas formuladas independientemente.

## Evidencia registrada

### Observado

Los siguientes hechos fueron observados directamente en material accesible o en
artefactos propios:

1. El objetivo público existe en TradingView en
   `https://es.tradingview.com/script/lzNtISvY/`.
2. TradingView presenta el código Pine del objetivo como protegido; su fuente no
   está disponible para inspección pública.
3. La descripción pública dice únicamente que el indicador mide volatilidad
   durante un periodo dado. La captura pública observada usa SPX500USD en 5
   minutos y muestra dos indicadores; esto no revela condiciones internas.
4. MacroFlow v0.4 contiene código Pine propio para régimen fundamental,
   transmisión entre mercados, microestructura, rango de apertura, VWAP anclado,
   pivotes confirmados, volumen relativo, costes aproximados y niveles de riesgo.
5. El código propio fue revisado manualmente y compiló en TradingView. La
   compilación demuestra validez sintáctica en ese entorno, no rentabilidad ni
   equivalencia con el objetivo público.
6. `REA inspect-artifact` no admite artefactos Pine Script. Por ese motivo no se
   utilizó como prueba automática del `.pine`; la revisión del código propio fue
   manual y la comprobación operativa se realizó mediante compilación en
   TradingView.

### Inferido

Estas proposiciones son hipótesis razonables derivadas de lo visible, pero no
están confirmadas por el código protegido:

1. Una caja de apertura puede utilizarse para medir expansión, aceptación,
   rechazo, ruptura, retesteo o falsa ruptura.
2. El ancho del rango, el volumen, la eficiencia de la vela y la distancia a
   VWAP o pivotes pueden aportar información adicional para clasificar una
   ruptura.
3. La confirmación entre marcos temporales y mercados relacionados puede reducir
   señales aisladas, aunque su valor incremental debe demostrarse fuera de
   muestra.
4. Los conceptos observables pueden combinarse con un régimen macro lento sin
   suponer que la microestructura mide directamente la economía.
5. La similitud visual o conceptual entre dos indicadores no demuestra que usen
   la misma fórmula, parámetros, datos, estados ni reglas de ejecución.

### Desconocido

No se conoce ni se presume lo siguiente sobre el objetivo protegido:

1. Fórmulas, pesos, umbrales o parámetros internos.
2. Reglas exactas de entrada, salida, stop, objetivos o gestión de posición.
3. Tratamiento de sesiones, zonas horarias, noticias, volumen o datos ausentes.
4. Uso de pivotes, medias, volatilidad, filtros multi-timeframe o confirmaciones.
5. Supuestos de comisiones, deslizamiento, liquidez o relleno de órdenes.
6. Resultados históricos reproducibles, muestras utilizadas o grado de
   sobreajuste.
7. Si sus señales repintan, usan datos no confirmados o cambian entre histórico
   y tiempo real.

## Identificadores de evidencia REA

Los siguientes identificadores quedan registrados como referencias inmutables
del proceso REA. Su presencia en este ledger no amplía por sí sola el contenido
observable ni concede acceso al código protegido.

| ID | Clasificación en este ledger |
|---|---|
| `ev_6641ea15f6c5701d25c4177d1076d6cf6837cd80dbb46559694d4102b3895333` | Listado del target público en la sesión REA |
| `ev_4b068f6dbec21c02b50abb2db85fd5b3f654604f6bd4f0247ae5eede6dda1064` | Inspección de la página pública y de sus límites observables |
| `ev_a6ead43272705b6f5df2cb0fcae10cafe1ddafe5f1a963321430ba329c95123e` | Análisis estático de tres bundles públicos de la página; 3.862 nodos AST y sin lógica Pine recuperable |

## Hipótesis preregistradas

Cada hipótesis debe compararse con una línea base y puede ser rechazada. Ninguna
se considera demostrada por haber sido implementada.

### H01 — Transformaciones

**Hipótesis:** normalizar cambios de variables macro y de transmisión mediante
transformaciones causales y escalas comparables mejora la estabilidad del score
frente al uso de niveles o variaciones sin normalizar.

- Tratamiento: cambios normalizados con ventanas que solo contienen datos
  disponibles en el momento de la señal.
- Control: variables sin transformar o microestructura sola.
- Métrica principal: expectativa neta OOS en unidades de R.
- Rechazo: no mejora la expectativa ni reduce drawdown de forma estable, o la
  mejora desaparece con perturbaciones razonables de la ventana.

### H02 — Frescura

**Hipótesis:** bloquear o degradar señales cuando la transmisión entre mercados
está desactualizada reduce pérdidas en BTC fuera del horario de los ETF
estadounidenses.

- Tratamiento: gate de antigüedad de la última observación confirmada.
- Control: `gaps_off` conservando indefinidamente el último valor.
- Métricas: expectativa neta, carga de costes y drawdown por franja horaria.
- Rechazo: el gate no mejora resultados OOS o elimina señales útiles sin reducir
  riesgo.

### H03 — Gates y cobertura

**Hipótesis:** exigir cobertura mínima y no convertir datos ausentes en valores
neutrales mejora la coherencia de los grados A/B.

- Tratamiento: `dataReady`, cobertura mínima y modelo reducido etiquetado por
  separado cuando falte una capa.
- Control: renormalización dinámica y sustitución de ausentes por cero.
- Métricas: expectativa y calibración por nivel de cobertura.
- Rechazo: no existe deterioro al operar con cobertura incompleta o el gate no
  aporta estabilidad OOS.

### H04 — Confirmación MTF (hipótesis base v0.4)

**Hipótesis:** exigir al menos dos de tres marcos temporales alineados mejora la
calidad de señales de ruptura o retesteo.

**Modificación REA-005:** v0.5 mantiene confirmación amplia para A, mientras B
Equilibrado/Activo puede usar al menos uno de los dos marcos rápidos. Esta es una
hipótesis nueva para medir el intercambio entre frecuencia y calidad; no debe
presentarse como confirmación de H04.

- Tratamiento: voto MTF causal con barras superiores ya confirmadas.
- Control: calcular MTF solo para mostrarlo, sin incorporarlo al gate.
- Métricas: expectativa neta, frecuencia, MAE, MFE y drawdown.
- Rechazo: la mejora no sobrevive al coste de reducir operaciones o depende de
  una única combinación de temporalidades.

### H05 — R:R ponderado

**Hipótesis:** calcular recompensa y costes según el porcentaje realmente cerrado
en TP1 y TP2 evita clasificaciones optimistas y mejora la selección de señales.

- Tratamiento: R:R neto ponderado por cantidades, con comisión y slippage por
  cada fill.
- Control: valorar el 100% de la posición como si saliera en TP2.
- Métricas: error entre R:R previsto y realizado, expectativa neta y proporción
  de señales reclasificadas.
- Rechazo: la ponderación no cambia decisiones ni reduce el error de previsión.

### H06 — Setup único

**Hipótesis:** permitir una sola operación por ruptura, barrido o evento de
estructura evita reentradas correlacionadas y reduce drawdown.

- Tratamiento: identificador de setup consumido después de la primera señal.
- Control: permitir nuevas entradas mientras la condición del mismo setup siga
  siendo verdadera.
- Métricas: expectativa, racha de pérdidas, concentración de operaciones y
  drawdown por evento.
- Rechazo: las reentradas independientes aportan expectativa OOS sin aumentar
  desproporcionadamente la pérdida por evento.

## Protocolo OOS compacto

1. **Unidad de prueba:** símbolo, venue, temporalidad, perfil, versión de código
   y configuración quedan fijados en cada corrida.
2. **Warm-up:** se excluyen de evaluación todas las barras necesarias para
   completar las ventanas macro, de transmisión y micro.
3. **Desarrollo:** el 80% inicial se utiliza para walk-forward; el 20% más
   reciente permanece como holdout final sin consultar.
4. **Walk-forward:** 24 meses de entrenamiento, 6 meses de validación y 3 meses
   de prueba, avanzando tres meses. Se aplica embargo igual a la duración máxima
   de una operación para evitar etiquetas solapadas entre bloques.
5. **Ejecución:** señal al cierre; primera ejecución posible en la apertura de la
   siguiente vela; costes por fill; slippage base y de estrés; gap más allá del
   stop ejecutado al primer precio disponible; stop primero cuando stop y target
   sean indistinguibles sin datos intrabar.
6. **Métrica primaria:** expectativa neta OOS en R. Métricas secundarias: profit
   factor neto, drawdown, MAE/MFE, duración, carga de costes, rachas y resultado
   por setup, grado y régimen.
7. **Estrés:** todas las operaciones como taker, slippage elevado, una vela de
   latencia, retirada de las mejores operaciones y perturbación de parámetros de
   al menos ±20%.
8. **Comparaciones:** microestructura base; base más transmisión; modelo completo.
   Cada combinación probada cuenta como ensayo. Los modelos finalistas requieren
   corrección por pruebas múltiples y estabilidad entre folds.
9. **Criterio de avance:** expectativa neta positiva bajo estrés, degradación
   razonable de validación a OOS, drawdown dentro del límite preregistrado y
   orden esperado de calidad `A > B > C`.
10. **Holdout:** después de observarlo no se reajusta la misma versión. Cualquier
    cambio genera una versión nueva y requiere datos futuros u otro holdout.

## Limitaciones de evidencia

- La compilación en TradingView valida sintaxis y ejecución del Pine propio;
  no valida rentabilidad, ausencia de sesgo ni equivalencia con otro indicador.
- Los históricos macro revisados pueden incorporar información que no estaba
  disponible originalmente. Una validación comercial debe utilizar vintages o
  datos fechados cuando sea posible.
- El calendario manual de tres eventos no representa un histórico completo y no
  puede sustentar una afirmación sobre desempeño alrededor de noticias.
- Los ETF de transmisión tienen horarios distintos de los mercados cripto; la
  frescura debe auditarse explícitamente.
- Las categorías A/B/C son etiquetas de calidad pendientes de calibración, no
  probabilidades de éxito.

## Declaración de procedencia

MacroFlow utiliza ideas generales y públicamente conocidas —rango de apertura,
ruptura, retesteo, falsa ruptura, pivotes, VWAP, volumen, volatilidad, relaciones
entre mercados y régimen macro— combinadas mediante reglas escritas de forma
independiente. Este ledger prohíbe presentar como observado cualquier detalle
que solo haya sido inferido del objetivo protegido y prohíbe atribuir a ese
objetivo reglas que no estén documentadas públicamente por su autor.

## Ampliación v0.5 — zonas y arquitectura multiagente

**Fecha de revisión:** 2026-10-15.

Fuentes públicas revisadas:

- emisión XAUUSD rotulada 1m de Daily Trading Tips:
  <https://www.youtube.com/watch?v=3H4IVQejlDE>;
- ficha pública de Zone Scalp PRO:
  <https://dailytrading.tips/indicators/D>;
- catálogo público de indicadores:
  <https://dailytrading.tips/indicators>;
- TradingAgents:
  <https://github.com/tauricresearch/tradingagents>.

### Observado

1. El enlace de YouTube es una emisión automática de XAUUSD, no una clase con la
   fórmula del indicador. La mascota es un gato, pero el canal se identifica como
   Daily Trading Tips.
2. La ficha pública de Zone Scalp PRO describe un giro de banda de tendencia,
   entrada y riesgo ATR congelados en la vela de señal, TP1/TP2/TP3 de 1R/2R/3R,
   una mejora de grado por EMA200, un panel MTF y una operación por dirección.
3. La misma ficha describe zonas no revisitadas y un primer retorno, y nombra
   sesión, EMA50, expansión de volumen, altura de zona y frescura como componentes
   de una puntuación. No publica sus ecuaciones, parámetros ni disparadores
   completos. El catálogo y la emisión muestran 1 minuto, mientras la ficha
   consultada muestra M15; por esa discrepancia no se atribuye un marco temporal
   canónico al producto.
4. TradingAgents es un framework Python/LangGraph bajo Apache-2.0. Separa
   analistas técnico, fundamental, noticias y sentimiento; debate alcista/bajista;
   propuesta del trader; revisión de riesgo; y decisión del gestor de cartera.
5. TradingAgents reconoce que sus resultados LLM no son deterministas y describe
   un backtest que evalúa decisiones fechadas.

### Implementación propia e inferida

MacroFlow v0.5 usa reglas originales y auditables basadas en conceptos generales:

- una reacción causal a EMA, VWAP, pivote confirmado o desequilibrio de tres
  velas fresco;
- puntuación técnica por patrón, estructura, MTF, volumen, costes y R:R;
- macro y transmisión como votos que mejoran o degradan el grado;
- noticia, stop, coste y cooldown como vetos separados;
- estados `WATCH`, `B` y `A`, con entrada y objetivos congelados al cierre.

Como límite inferido de ingeniería, ese backtest no sustituye por sí solo un
simulador independiente de fills, comisiones, slippage, funding y liquidaciones
para scalping. En MacroFlow, entrada, stop inicial y objetivos se fijan al cierre;
tras TP1 el stop monitorizado pasa a breakeven desde la vela siguiente.

Estas reglas no son una copia ni una reconstrucción de Zone Scalp PRO. El código
privado, la fórmula de su banda, sus ventanas de sesión, ponderaciones, umbrales,
tratamiento de zonas e invalidaciones siguen siendo desconocidos.

De TradingAgents se adopta la separación conceptual entre evidencia, debate,
propuesta y riesgo. No se incorpora código del repositorio en Pine, no se afirma
afiliación y no se simula que TradingView pueda ejecutar agentes LLM o consumir
noticias externas en tiempo real.

## Ampliación v0.6 — pullback y sobreextensión

**Fecha de revisión:** 2026-10-15.

Fuentes públicas revisadas: currículo y webinar de riesgo de WSGTA, títulos
públicos de sus clases y un ejemplo publicado de David Green. Confirman el uso
general de medias, VWAP, ATR, stops, parciales y setups de tendencia o precio
alejado de la media; no publican las cinco reglas completas ni resultados
auditados.

- <https://www.wsgta.com/lessons>
- <https://www.wsgta.com/risk-management-webinar>
- <https://www.youtube.com/@wsgta>
- <https://www.benzinga.com/trading-ideas/long-ideas/21/10/23217955/how-david-green-traded-moderna-for-a-big-profit-on-monday-morning>

MacroFlow v0.6 implementa hipótesis propias y comprobables: EMA65 configurable,
`TREND_PULLBACK` con recuperación confirmada de EMA/VWAP/estructura y `EXT_FADE`
con distancia en ATR, RSI extremo y confirmación en la vela posterior. `EXT_FADE`
queda limitado a B/WATCH hasta superar validación fuera de muestra. No se usan
pivotes “institucionales”, Fibonacci secreto ni order flow simulado, y no se
afirma que estas reglas reproduzcan el sistema privado de WSGTA.
