# Kronos como autor (`ia:kronos`)

[Kronos](https://github.com/shiyu-coder/Kronos) (MIT, AAAI 2026) es un modelo
base entrenado con velas de más de 45 bolsas. Muestrea las siguientes velas
como un modelo de lenguaje muestrea palabras. Aquí compite en el ledger como un
autor más: antes de que abra la ventana de una pregunta genera muchos
futuros posibles y dice en qué fracción se cumple.

## La trampa que evita

`KronosPredictor.predict` **promedia** los caminos muestreados
(`np.mean(preds, axis=1)` en `model/kronos.py`). Un camino promedio es mucho
más suave que cualquier camino real, así que casi nunca "toca" un nivel.
`propose.py` pide los caminos uno por uno (`predict_batch` con
`sample_count=1`) y juzga cada uno con `resolver.step_outcome`, la misma regla
que resuelve las velas reales. Las velas de antes de la ventana no cuentan, y
una vela muestreada incoherente se corrige (máximo y mínimo de sus cuatro
precios).

## Pasos (en tu PC, antes de que abra la ventana)

```powershell
# Una vez: Kronos junto a CALIOPE y un entorno aparte
cd ..
git clone https://github.com/shiyu-coder/Kronos
cd CALIOPE
python -m venv .venv-kronos
.venv-kronos\Scripts\activate
pip install -r labs/kronos_author/requirements.txt

# Velas hasta ahora, del intervalo de la pregunta (--start = ahora menos unas horas)
python tools/fetch_klines.py --symbol BTCUSDT --interval 15m --lookback-hours 120 `
  --start <hace 2 horas> --end <ahora> --out projects/macroflow/predictions/data/kronos_<fecha>.json

python labs/kronos_author/propose.py --ledger projects/macroflow/predictions/ledger.jsonl `
  --question q-... --klines projects/macroflow/predictions/data/kronos_<fecha>.json
deactivate
```

La salida trae la probabilidad, cuántos caminos cumplieron y el comando
`forecast --author ia:kronos` listo. **No escribe en el ledger**: el modelo
propone y tú decides si sellas. La probabilidad se recorta a [1%, 99%], porque
200 caminos no justifican un 0% ni un 100%.

Opciones: `--paths 200` (más caminos, más estable y más lento), `--batch 25`,
`--model NeoQuasar/Kronos-mini` (más rápido) o `Kronos-base`, `--seed`,
`--temperature`, `--top-p`.

## Verificado y sin verificar

- Verificado aquí: la lógica (historia, horizonte, juicio de caminos y
  conteo) con un modelo falso, y el cableado con el código real de Kronos y
  un modelo diminuto de pesos aleatorios. `predict_batch` con
  `sample_count=1` devolvió caminos distintos entre sí, de la longitud
  pedida.
- Sin verificar: los pesos reales (Hugging Face está bloqueado en el entorno
  del agente), el tiempo en tu CPU y, sobre todo, **si Kronos acierta**. Eso
  lo dirá `rival` contra la historia, la volatilidad y el bootstrap después
  de unos 30 resultados.
