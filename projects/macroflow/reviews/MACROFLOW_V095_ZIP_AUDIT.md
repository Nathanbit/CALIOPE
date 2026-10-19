# Auditoría estática — `macroflow_v0.9.5.zip`

Fecha: 2026-10-18 (America/Guayaquil)

## Identidad y alcance

- Origen: `C:\Users\natha\Downloads\THEMIS\macroflow_v0.9.5.zip`
- SHA-256 del ZIP: `7630C65966E1EB1311F9F6D49B9E434FBF4AEBC1680A760FF65BC9FA6599D3E9`
- Tamaño: 73,288 bytes.
- Contenido: 45 archivos; Python, Pine, HTML, JSON, BAT/SH y un log de texto.
- Extracción usada solo para auditoría: `audit_tmp/macroflow_v095_20261018_202143/`.
- No se ejecutó `run_local.bat`, `run_local.sh`, el servidor, MT5 ni órdenes.

La inspección no encontró rutas absolutas, `..`, enlaces simbólicos, binarios, claves privadas ni credenciales reales. `server.log` solo contiene un fallo de arranque por ausencia de `.venv/bin/python`. No hay indicios estáticos de malware. Esto no equivale a certificar el sistema para trading real.

## Resultado de verificaciones

- Los 27 módulos Python compilan con `compileall` sin errores sintácticos.
- Prueba de humo local sobre datos sintéticos: 9,000 barras, 121 trades; sizing y Monte Carlo terminaron sin excepción.
- La suite `pytest` no se ejecutó porque el runtime disponible no incluye `pytest`; no se instalaron paquetes externos durante esta auditoría.
- El proyecto no incluye lockfile, hashes de dependencias, licencia ni historial Git.

## Diferencia material frente al indicador activo

El ZIP no contiene una versión completa del Pine actual:

| Elemento | Pine activo del workspace | Pine del ZIP |
|---|---:|---:|
| Líneas | 2,090 | 38 |
| Inputs | 136 | 10 |
| `request.security()` | 19 | 0 |
| SHA-256 | `63CFF54E2A36A3A3B5DB2AAA812D0F1BCF70D751D579D566DFB18A26DEA4816C` | `D3ECBF4D6A7C6FD9D8113092DA1176443EAF5714CF0AEC17B2331493D8546EBA` |

El propio archivo del ZIP dice que es un **parche**, no el script completo. Además, el paquete declara versión `0.9.4`, usa `0.9.4` como modelo base y solo llamaría `0.9.5` al primer candidato generado. Por tanto, el nombre del ZIP no prueba que contenga una estrategia v0.9.5 lista para TradingView.

## Hallazgos bloqueantes

### 1. La métrica promovida no pertenece al modelo que se registra

En `optimization/recursive.py`, cada fold produce parámetros distintos y sus trades OOS se concatenan. Después, las métricas conjuntas se asignan a `last_best`, que son únicamente los parámetros del último fold, y ese `last_best` es el que se registra y promueve. El sistema evalúa un conjunto de modelos variables y despliega otro modelo único. Los gates, PF, esperanza e IC no validan el artefacto promovido.

### 2. El supuesto OOS fresco se reutiliza

`last_data_end` solo evita repetir una corrida cuando no cambia la última barra. Cuando llega una sola barra nueva, se reconstruyen y vuelven a usar todos los folds históricos. Así, los mismos tramos OOS influyen repetidamente en búsqueda, selección y estrechamiento del espacio. No es un holdout de un solo uso y acumula sobreajuste de selección.

### 3. El motor Python no replica MacroFlow v0.9.4

`strategy/reference.py` implementa una estrategia reducida de tendencia, Pearson y VIX. Omite, entre otros, el motor WATCH/promoción, los 19 contextos multitemporales, gran parte del régimen macro, grados A/B/C reales, salidas parciales, trailing/breakeven, MFE/MAE y las reglas de contexto cerrado del Pine completo.

También difiere en ejecución y costes:

- Pine activo: `process_orders_on_close=true`; réplica: llena en la apertura siguiente.
- Pine activo: comisión 0.08 % por fill más slippage modelado 0.02 % por lado; réplica: `cost_bps=6`, equivalente a un supuesto distinto y menor.
- Pine activo ejecuta varias salidas; réplica solo stop o cruce estructural.

Una optimización exitosa de esa réplica no demuestra mejora del indicador activo.

### 4. Promoción automática peligrosa y basada por defecto en demo

Aunque `OptimizerConfig.auto_approve` empieza en `False`, `LoopConfig`, el API y el panel usan `auto_approve=True` por defecto. La fuente predeterminada es `demo`, una serie sintética determinista que mostró PF 2.84 en la prueba de humo. El sistema puede activar versiones basadas en datos artificiales.

Al activarlas cambia inmediatamente `active_model_version` del puente, pero TradingView no puede recibir el Pine automáticamente. Las alertas de la versión anterior quedan rechazadas. Peor aún, el Pine generado parte del parche incompleto de 38 líneas.

### 5. La auditoría por plan no participa en el bucle real

`RecursiveOptimizer` acepta `plans_provider`, pero `AutoLoop._ensure_optimizer()` nunca lo conecta. Además, `blocked_grades` se añade al espacio fijo y la réplica no lo aplica. La documentación promete una auditoría que, en el camino autónomo, está inactiva.

## Riesgos de ejecución y seguridad

1. Existe un adaptador MT5 capaz de enviar órdenes reales. El endpoint llamado “alerta de prueba” usa el broker configurado; si `MF_BROKER=mt5`, puede colocar una operación real.
2. El secreto predeterminado es `change-me` y no hay un fail-fast al activar MT5. La allowlist de TradingView está desactivada por defecto.
3. El webhook público parsea el cuerpo antes de autenticarlo, no fija tamaño máximo ni rate limit y acepta timestamps futuros, porque solo rechaza `age > 120`.
4. El sizing usa el precio comunicado por TradingView, mientras la orden se llena al bid/ask real. No hay gate por spread, desviación entre alerta y tick, stop-level del broker, riesgo agregado, número máximo de posiciones ni exposición correlacionada.
5. `close_all()` cierra todas las posiciones del símbolo con el mismo `magic`; no identifica un plan concreto.
6. Los scripts de arranque instalan dependencias con rangos abiertos y sin lockfile/hashes, por lo que dos instalaciones pueden ejecutar código distinto.
7. El reparto 50/30/20 pierde el remanente producido por redondeo. Ejemplo probado: 0.05 lotes se convierte en 0.02 + 0.01 + 0.01 = 0.04. Reduce riesgo, pero falsea volumen y métricas.

## Dictamen

**Útil como prototipo de infraestructura, no apto todavía para automejora ni ejecución real.** Mantenerlo en cuarentena y con broker `paper`. No iniciar MT5 ni `auto_approve` hasta corregir los hallazgos bloqueantes y repetir validación reproducible.

## Orden recomendado de consolidación en CALIOPE

1. Importar el código como rama/propuesta, no como versión activa.
2. Sustituir el parche Pine por el script completo versionado y crear pruebas de equivalencia barra a barra entre Pine y Python.
3. Evaluar **un único conjunto fijo de parámetros desplegable** en cada fold. Usar nested walk-forward para seleccionar y reservar un holdout final de un solo uso.
4. Registrar dataset, rango, zona horaria, costes, commit, hash del Pine, parámetros y semillas en cada evidencia.
5. Dejar `paper`, `auto_approve=false` y riesgo 0 como defaults. Una IA puede proponer; otras pueden revisar estadística, Pine, riesgo y seguridad; un validador determinista debe aceptar/rechazar. La activación con capital real requiere una aprobación humana explícita.
6. Dar a agentes NVIDIA/otras IAs acceso de solo lectura a evidencia y propuestas, nunca a credenciales, broker ni `order_send`.
7. Añadir gates operativos: equivalencia Pine/Python, pruebas completas, OOS no reutilizado, estabilidad entre activos/regímenes, límites de exposición y prueba demo prolongada.

