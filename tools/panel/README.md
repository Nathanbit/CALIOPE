# Panel de CALIOPE

Página local para sellar predicciones rápido y ver el ledger. Dictas (o
escribes) "BTC toca 82.500 hoy, 60%", el panel propone el concepto y te deja
los comandos de PowerShell para `seal --dry-run` y `seal`. **No escribe en el
ledger ni coloca órdenes**: el sellado lo hace `prediction_ledger.py`, con
sus compuertas, y solo cuando tú pegas el comando.

## Abrirlo

Desde la carpeta CALIOPE:

```powershell
python -m http.server 8765 --bind 127.0.0.1
```

y abre <http://127.0.0.1:8765/tools/panel/>. `--bind 127.0.0.1` importa:
sin eso, cualquiera en tu red podría leer la carpeta. El micrófono solo
funciona en `127.0.0.1`/`localhost` (contexto seguro), no abriendo el archivo
con doble clic.

## Pestañas

- **Sellar**: dictar o escribir, revisar campos y copiar los comandos.
- **Preguntas**: las abiertas, con cuenta regresiva hasta que abre o cierra
  la ventana y los pronósticos de cada autor. Las que ya cerraron traen los
  comandos de `fetch_klines` y `resolve-auto`. Las resueltas muestran el
  Brier de cada autor y lo que decía la volatilidad.
- **Marcador**: Brier por autor contra la moneda y contra el rival de
  volatilidad en las mismas preguntas, el enjambre, el gráfico de
  calibración ("cuando dices 70%, ¿pasa el 70%?") y el veredicto con
  intervalos de `rival`. Este último se lee de `state/rival.json`, que se
  genera en la terminal:

```powershell
python prediction_ledger.py --ledger projects/macroflow/predictions/ledger.jsonl rival | Out-File -Encoding utf8 state\rival.json
```

La lógica de lectura (`ledger.js`) replica la de `forecast_scoring.py`: el
primer pronóstico de cada autor puntúa, `void` no, y el enjambre es la media
en log-odds. Se prueba con Node en `ledger.test.mjs`.

## Qué hace cada parte

- **Dictar**: Whisper corre en tu navegador con Transformers.js 4.3.1; el
  audio no sale de tu PC. La primera vez descarga el modelo (~80 MB el base,
  ~40 MB el tiny). Ajustes tomados de
  [runonweb.ai](https://github.com/midudev/runonweb.ai) (MIT): WASM con `q8`
  (en WebGPU `q8` da basura sin avisar) y un solo hilo si la página no está
  aislada. Un modelo puede oír mal un número: revisa siempre el texto.
- **Interpretar** (`parse.js`): lee activo, evento (toca, cierra, toca uno
  antes que otro), niveles ("82.500", "82k500", "82 mil quinientos"),
  probabilidad ("60%", "60 por ciento") y plazo ("hoy", "mañana", "el
  domingo", "en 4 horas", "a las 7 de la noche"), en tu hora local. Lo que no
  entiende lo deja vacío; nunca lo inventa.
- **Leer de Binance**: el último precio de la API pública desde tu navegador,
  sin claves, con la hora como fuente. Si no carga, escribe el de tu
  pantalla y de dónde salió.
- **Sellar**: el inicio de la ventana es la siguiente vela con al menos 3
  minutos de margen (velas de 1 minuto si el plazo es ≤ 6 h; de 15 si no).
  El concepto se escribe en `state\concepto.json` (no versionado) en ASCII,
  para que PowerShell 5 no le añada BOM.
- **Ledger**: tabla de preguntas, pronósticos y Brier por autor. Es solo
  lectura y no comprueba la cadena de hashes: la autoridad es `verify` y
  `rival`.

## Seguridad del código de terceros

El panel no guarda claves ni firma nada, pero sí ejecuta un script ajeno:
transformers.js (la voz), desde jsdelivr. El 29 de octubre de 2026 Polymarket
perdió fondos de usuarios porque un script de terceros (DebugBear) fue
reemplazado por un drenador con la misma URL. Por eso:

- La URL fija una versión exacta (`@4.3.1`) y el import map de `index.html`
  exige su SHA-384. Si el archivo cambia, el navegador se niega a ejecutarlo
  y la voz falla con un error de integridad.
- `tests/test_panel.py` rechaza cualquier script remoto sin versión o sin hash.
- Lo que queda sin hash: los archivos que transformers.js descarga después
  (onnxruntime-web y los pesos del modelo de Hugging Face). Si no usas la voz,
  no se descarga nada de terceros.
- Las claves de la mesa nunca pasan por el navegador: viven en variables de
  entorno y solo las lee `python -m desk`.

Para subir de versión:

```bash
npm pack @huggingface/transformers@X.Y.Z
tar xzf huggingface-transformers-X.Y.Z.tgz package/dist/transformers.min.js
echo "sha384-$(openssl dgst -sha384 -binary package/dist/transformers.min.js | base64 -w0)"
```

y cambia la URL y el hash juntos.

## Pruebas

`tests/test_panel.py` ejecuta `parse.test.mjs` con Node si está instalado
(si no, se omite y lo dice).
