# Laboratorio causal: ¿qué anticipa a BTC?

Experimento aparte del núcleo de CALIOPE. Usa
[bloomberg/causal-ts](https://github.com/bloomberg/causal-ts) (GPL-3.0,
PyTorch) para buscar series que se muevan **antes** que BTC, y después
comprueba de forma honesta si esas relaciones se sostienen en datos que el
descubrimiento nunca vio. Nada de esto toca el ledger ni coloca órdenes.

## Por qué así

[bhoult/correlation](https://github.com/bhoult/correlation) calculó 1,57
billones de correlaciones con retraso entre acciones y probó 1.753 series
externas: aparecían al ritmo que produce el azar. Por eso aquí:

1. **El holdout se fija antes de mirar.** Los últimos 180 días alineados
   (`--holdout-days`) quedan fuera del descubrimiento.
2. **Las hipótesis se congelan.** `discover.py` escribe un archivo nuevo en
   `hypotheses/` con el hash de los datos. Se hace commit **antes** de
   evaluar, así el historial de git muestra que no se cambiaron al ver el
   resultado.
3. **Se evalúa contra el azar.** `evaluate.py` compara cada relación con
   permutaciones por bloques de 5 días y corrige por el número de relaciones
   (Bonferroni).
4. **Solo retrasos de 1 día o más.** Lo que se mueve el mismo día no sirve
   para predecir.
5. **Un día con un dato faltante se descarta**, nunca se rellena.

## Pasos (en tu PC)

```powershell
# 1. Datos públicos, sin claves (solo biblioteca estándar)
python labs/causal_btc/fetch_series.py --start 2020-01-01

# 2. Descubrimiento (entorno aparte: causal-ts trae PyTorch)
python -m venv .venv-causal
.venv-causal\Scripts\activate
pip install -r labs/causal_btc/requirements.txt
python labs/causal_btc/discover.py --data labs/causal_btc/data/series_<fecha>.csv
deactivate

# 3. Congelar las hipótesis antes de ver si se sostienen
git add labs/causal_btc/data labs/causal_btc/hypotheses
git commit -m "lab: hipótesis causales congeladas"

# 4. Validación en el holdout (solo biblioteca estándar)
python labs/causal_btc/evaluate.py --hypotheses labs/causal_btc/hypotheses/<archivo>.json
```

## Series

| Columna | Fuente |
|---|---|
| `btc`, `eth` | Binance, velas diarias (cierre a las 00:00 UTC) |
| `btc_funding` | Binance perpetuo USDT-M, media diaria del funding |
| `ndx`, `spx`, `gold`, `us10y` | Stooq, CSV diario |

Si una fuente no responde, su columna queda fuera y se informa el motivo.
Faltan los flujos de los ETF de bitcoin: no hay una fuente pública y estable
sin claves.

## Cómo leer el resultado

- `holds: true` quiere decir que, en el holdout, la relación le ganó al azar
  después de corregir por muchas pruebas. Eso **todavía no** es una forma de
  ganar dinero: falta restar costos y comprobarla hacia adelante.
- El paso siguiente para una relación que se sostiene es convertirla en un
  autor (`indicador:causal-ndx`, por ejemplo) que selle predicciones antes de
  cada ventana. El ledger y `rival` dicen después si aporta algo frente a la
  historia y a la volatilidad.
- Si nada se sostiene, es el resultado esperado, y también vale: ahorra
  operar con una señal falsa.

## Sin verificar

- La descarga contra Binance y Stooq reales (el entorno del agente no llega
  a esos sitios).
- Las pruebas usan datos sintéticos: una relación real que debe sostenerse y
  ruido que no.
