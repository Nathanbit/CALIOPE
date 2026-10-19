# MacroFlow v0.9.4 — auditoría de comportamiento, corrección WATCH y premarket Asia

**Modelo:** `MF-0.9.4-REA-VAL`  
**Experimento:** `REA-0065`  
**Fecha de corte:** 18 de octubre de 2026 (America/Guayaquil)  
**Unidad estadística:** plan completo, no cada salida parcial

## Dictamen ejecutivo

La evidencia disponible no permite afirmar todavía que v0.9.4 sea rentable. Sí permite identificar un defecto causal en v0.9.3 y verificar que v0.9.4 corrige su arquitectura sin ampliar el espacio de parámetros: conserva los 136 inputs y las 19 lecturas `request.security()`.

El defecto principal de v0.9.3 era la mezcla de dos planes. Una WATCH nacía con entrada, stop y objetivos congelados, pero al promoverla se ejecutaba al cierre posterior usando el stop candidato de esa nueva barra y se reconstruían los TP. El tamaño también se calculaba con el riesgo candidato del bar, no necesariamente con la distancia del plan finalmente ejecutado. Esta inconsistencia podía convertir evidencia antigua en una entrada tardía con geometría distinta.

v0.9.4 mantiene los niveles de la WATCH, vuelve a calcular desde el precio de promoción únicamente el riesgo y la recompensa que aún quedan, exige objetivos todavía alcanzables, RR neto y coste válidos, stop dentro de límites ATR y confirmación local en la dirección de EMA rápida/lenta. También impide sobrescribir una WATCH activa y calcula el lote con la distancia real entre `setupEntry` y `setupStop`.

El contexto de Asia es defensivo y de baja confirmación regional: Wall Street cerró el viernes con ventas concentradas en tecnología, VIX al alza y BTC sostenido cerca del soporte del fin de semana. Hong Kong estará cerrado el lunes y el bloque macro chino se publica a las 21:00 de Guayaquil. En ese contexto, SPY/VIX deben tratarse como contexto cerrado; no como datos direccionales en vivo.

## Evidencia de comportamiento observada

| Versión | Ventana / estado | WATCH | Entradas / planes | Resultado |
|---|---|---:|---:|---|
| v0.9.2 | prueba diagnóstica previa | 103 | 0 | El filtro era tan restrictivo que no se podía evaluar la ejecución. |
| v0.9.3 | prueba previa, BTCUSDT 1m | no comparable | 4 cortos | 0 ganadores; P&L `-23,95 USD`; PF `0`. |
| v0.9.3 | recarga 11–18 oct. | no comparable | 0 | La diferencia de ventana impide usarla como réplica de los cuatro trades. |
| v0.9.4 | BTCUSDT 1m, 11–18 oct. 2026 | no expuesto por el informe | 0 | Sin operaciones; P&L/PF/DD no aplican. |

Las cuatro pérdidas de v0.9.3 son una alerta de implementación y selección, no una muestra estadística. No justifican optimizar umbrales, añadir filtros ad hoc ni concluir que el patrón carece de ventaja.

### Métricas que debe completar la captura final de TradingView

- Símbolo/feed: **BINANCE:BTCUSDT**.
- Temporalidad: **1 minuto**.
- Rango exacto del informe: **11–18 de octubre de 2026**.
- WATCH / promociones / entradas: el Strategy Tester solo confirma **0 entradas**; W/P no se muestran en esa vista.
- Invalidadas / expiradas: no visibles en el informe final guardado.
- Planes completos: **0**.
- P&L neto y ajustado, Profit Factor, drawdown, win rate y MFE/MAE: **no aplican porque N=0**.
- Captura: `macroflow/evidence/MacroFlow_v0_9_4_no_trades_2026-10-11_18.png`.

Si v0.9.4 produce cero operaciones en el mismo rango, el resultado correcto es: “el parche bloqueó las promociones que ya no conservaban una oportunidad ejecutable”; no “mejoró la rentabilidad”. Para comparar rentabilidad, v0.9.3 y v0.9.4 deben ejecutarse sobre el mismo símbolo, timeframe, rango y propiedades del tester.

## Causa raíz de v0.9.3

1. **Plan híbrido.** La WATCH guardaba `watchEntry`, `watchStop` y `watchTp*`, pero al promocionarse asignaba `setupStop` desde `longCandidateStop`/`shortCandidateStop` de la barra posterior y reconstruía objetivos desde esa geometría.
2. **Economía no revalidada sobre el plan original.** Los gates de coste, stop y RR procedían del candidato actual; no de la recompensa restante frente a los niveles congelados.
3. **Tamaño potencialmente desalineado.** El lotaje usaba `longRisk`/`shortRisk`, aunque el stop de la entrada podía pertenecer a otra geometría.
4. **Sin confirmación direccional local explícita.** La coincidencia MTF/micro podía promover una WATCH aun cuando la vela de entrada y el orden EMA local no confirmaban el lado.
5. **Sobrescritura de estado.** Una WATCH nueva podía reemplazar otra pendiente antes de que expirara o se invalidara.
6. **Telemetría de universos distintos.** Los contadores W/P/E podían incluir observaciones fuera del rango del Strategy Tester, por lo que el embudo visible no era directamente comparable con los trades.

## Correcciones verificadas en v0.9.4

- Identidad actualizada a `MF-0.9.4-REA-VAL` y experimento `REA-0065`.
- `BREAK_WATCH` se atribuye al bucket estable `BREAK`.
- WATCH y Strategy Tester comparten `modelAuditInDateRange`.
- Expiración e invalidación tienen contadores separados.
- El reset, invalidación, expiración y promoción limpian todos los campos de WATCH.
- Los objetivos congelados deben continuar delante del precio de entrada.
- RR neto y carga de costes se recalculan desde el cierre confirmado de promoción hasta los niveles originales.
- El stop restante debe quedar entre los límites ATR configurados.
- Long exige `close > emaFast > emaSlow` y vela alcista; short exige la relación inversa y vela bajista.
- Una promoción conserva `watchStop` y `watchTp1/2/3`.
- El tamaño usa `abs(setupEntry - setupStop)` más costes de ida y vuelta.
- Una WATCH solo nace cuando `watchDirection == 0`.
- El dashboard muestra RR, coste y score revalidados; además separa W/P/E e invalidadas/expiradas.

## Auditoría estática del artefacto

- Archivo: `macroflow/MacroFlow_Backtest_v0_9_VALIDATION.pine`
- Pine Script: v6.
- Líneas: 2.200.
- Inputs: 136 en v0.9.3 y 136 en v0.9.4.
- `request.security()`: 19 en ambas versiones.
- SHA-256 v0.9.3 archivada: `DF53074689A7E6A663F4E64C9050078FBCF9F56C235DE1E2E0F1380D2BF1EB0F`.
- SHA-256 v0.9.4 auditada: `63CFF54E2A36A3A3B5DB2AAA812D0F1BCF70D751D579D566DFB18A26DEA4816C`.
- Estado visual: `MF BT V094` compiló sin nuevas advertencias y quedó activo en TradingView; el informe confirmó cero operaciones en el rango indicado.

### Hallazgo residual resuelto

La asimetría de `shortWatchStopValidNow` quedó corregida: long y short validan ahora `watchStop > 0`. También se sustituyeron los dos refrescos de tabla basados en `barstate.islast` por una condición de barra confirmada, eliminando las últimas advertencias de compilación sin activar cálculo intrabar.

## Premarket Asia: hechos e implicaciones

### Hechos verificados al corte

- S&P 500 cerró el viernes en 7.659,44 (`-1,09%`); Nasdaq en 26.140,03 (`-2,15%`); Russell 2000 en 2.721,37 (`-1,66%`).
- VIX cerró en 19,99 (`+21,45%`) y WTI en 99,00 USD (`+2,59%`).
- La venta fue más intensa en tecnología y semiconductores; no fue una liquidación uniforme de todos los sectores.
- BTC cotizó el domingo aproximadamente entre 80,8k y 82,2k, con soporte repetido alrededor de 80,7k–80,8k.
- Tokio y Seúl abren alrededor de las 19:00 de Guayaquil; Shanghái alrededor de las 20:30.
- El bloque chino de PIB, producción industrial, ventas minoristas, inversión fija y desempleo se publica a las 21:00 de Guayaquil.
- Hong Kong estará cerrado el lunes por el día posterior al Chung Yeung Festival; la confirmación regional será menor de lo normal.

### Aplicación al modelo

- Mantener `US CLOSED` y riesgo reducido; no sumar SPY/VIX congelados como evidencia viva.
- Blackout recomendado para este evento: 20:45–21:30 de Guayaquil.
- Invalidar toda WATCH anterior al blackout; exigir una señal nueva después del dato.
- Esperar dos cierres confirmados después del evento antes de restaurar una señal B.
- Rechazar la primera vela si su True Range supera `1,5 × ATR` o la mecha dominante excede 60% del rango.
- Usar el propio precio/volumen/MTF del activo como evidencia principal durante Asia.
- Tratar 80,7k–80,8k como nivel de confirmación bajista solo con cierre/volumen; 81,5k–81,7k como primera recuperación y 82,1k–82,3k como aceptación superior. Son zonas de validación, no órdenes automáticas.

## Protocolo de validación y auto-mejora orquestada

Caliope debe tratar cada cambio como una hipótesis versionada, no como una edición acumulativa del “mejor” backtest:

1. **Congelar artefacto:** Pine, inputs, propiedades del tester, símbolo, timeframe, fechas y hashes.
2. **Asignar revisores independientes:** causalidad/no-repaint, ejecución/riesgo, estadística, contexto de mercado y red-team.
3. **Proponer una sola mutación causal:** nunca ajustar varios gates después de observar el holdout.
4. **Ejecutar pruebas funcionales:** Replay, niveles congelados, sizing, cierres 50/30/20, alertas y Daily Max Loss.
5. **Comparar cronológicamente:** desarrollo, validación y holdout con purga/embargo; el holdout no se reutiliza para rediseño.
6. **Aplicar compuertas mecánicas:** si una falla, rechazar o volver a experimento; el consenso de IAs no sustituye métricas.
7. **Registrar también los fallos:** commit, experimento, razón de rechazo y evidencia. Evita repetir variantes descartadas.

### Criterios mínimos

- Compilación v6 sin errores y prueba Replay sin desplazamiento de señales cerradas.
- Mismo universo temporal para contadores y operaciones.
- Contabilidad por plan completo reconciliada con fills y costes.
- Al menos 30 planes por setup/grado/lado antes de juzgar un bucket.
- Al menos 100 planes completos antes de interpretar el agregado.
- P&L ajustado y expectativa positivos fuera de muestra.
- Drawdown no materialmente peor que EMA baseline.
- Supervivencia a costes `1,5×`; estabilidad razonable a `2×`.
- Resultado no concentrado en un solo activo, sesión, mes o setup.
- Toda mejora experimental debe vencer al baseline en más de una ventana walk-forward.

## Fuentes de contexto

- AP, cierre de índices del 16 de octubre: <https://apnews.com/article/c27809783ae3230b44c26e5ee4bea056>
- AP, amplitud y caída tecnológica: <https://apnews.com/article/d2655f64db7ec8ae450525d58f97b628>
- Market digest, VIX y WTI: <https://stockmarketwatch.com/digest/2026-10-16>
- NBS China, calendario oficial: <https://www.stats.gov.cn/english/PressRelease/ReleaseCalendar/202512/t20251226_1962154.html>
- Trading Economics, calendario de China: <https://tradingeconomics.com/china/calendar>
- Gobierno de Hong Kong, feriados 2026: <https://www.info.gov.hk/gia/general/202505/16/P2025051300353.htm>

## Conclusión

v0.9.4 corrige una inconsistencia real de estado, riesgo y ejecución en v0.9.3. La mejora es estructural: una WATCH vuelve a ser un único plan causal desde su nacimiento hasta su promoción o descarte. TradingView confirmó que el parche eliminó las operaciones de la ventana visible, pero N=0 impide medir rentabilidad. La siguiente decisión debe depender de una muestra fuera de periodo, no de las cuatro pérdidas históricas ni de una votación entre IAs.
