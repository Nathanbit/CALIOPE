# Polymarket como rival de mercado (`mercado:polymarket`)

En Polymarket la gente apuesta dinero real en preguntas como "Bitcoin above
82,000 on October 28?". El precio del "Sí" es la probabilidad que el mercado
le da. Si ni tú, ni Claude, ni Kronos le ganan a ese precio, el mercado ya
sabe lo que sabemos.

## Por qué el espejo es exacto

Esos mercados se resuelven así (de sus reglas): "the Binance 1 minute candle
for BTC/USDT 12:00 in the ET timezone (noon) on the date specified in the
title has a final 'Close' price higher than the price specified in the
title". En CALIOPE eso es `close_beyond`, lado `above`, con velas de `1m` y
una ventana de una sola vela que empieza a las 12:00 de Nueva York. `above`
también es estrictamente mayor, así que un cierre igual al nivel es "No" en
los dos sitios. Las 12:00 de Nueva York se pasan a UTC con la regla de horario
de verano de EE. UU., sin base de zonas horarias (Python en Windows no la
trae sin el paquete `tzdata`).

## Pasos

```powershell
# El slug es la última parte de la URL del mercado en polymarket.com
python labs/polymarket/mirror.py --slug bitcoin-above-82k-on-october-28
```

Imprime el concepto, la probabilidad del mercado (punto medio entre la mejor
oferta de compra y la de venta, o el precio listado del "Sí") y los comandos
de PowerShell para sellar la pregunta con autor `mercado:polymarket`. El
precio de referencia se lee de la API pública de Binance; si no carga, pásalo
con `--reference-price` y `--reference-source`. **No escribe en el ledger.**

Después, antes de las 12:00 de Nueva York, cada autor añade su pronóstico a
la misma pregunta (`forecast --question q-...`, o el panel). Se resuelve
como cualquier otra, con `fetch_klines.py --interval 1m` y `resolve-auto`.

## Límites

- El precio del mercado incluye el margen entre compra y venta, y las
  comisiones. Es la mejor probabilidad pública con dinero detrás, no una
  verdad.
- Solo los mercados "Bitcoin above X on <fecha>" tienen espejo exacto. Los de
  "sube o baja en la hora" u otros activos necesitarían su propio espejo.
- Sin verificar: la API real de Polymarket (Gamma) y de Binance; el entorno
  del agente no llega a esos sitios. El formato (`outcomes`,
  `outcomePrices`, `bestBid`, `bestAsk`) está probado con datos de ejemplo.
