# La mesa (`desk/`): CALIOPE como la persona en el medio

Los agentes (tú, Claude, Kronos, MacroFlow, Polymarket…) **proponen** órdenes.
La mesa las pasa por compuertas, tú las **apruebas** en tu terminal y solo
entonces salen a BloFin. Cada paso queda sellado en un ledger encadenado
(`projects/macroflow/desk/orders.jsonl`), igual que las predicciones.

## Las compuertas

| Momento | Qué se exige |
|---|---|
| Proponer | Un **pronóstico sellado** del autor sobre esa pregunta; **stop-loss** obligatorio y del lado correcto; margen ≤ **20%** del capital; apalancamiento ≤ **5x**; si salta el stop no se pierde más del **5%** del capital; lectura de capital de hace menos de 10 min; mesa no apagada; pérdida del día < **5%** |
| Aprobar | Solo `nathan`, escribiendo `APRUEBO o-…` en su terminal, en menos de 10 min desde la propuesta. Automático solo si `auto.enabled` y el autor le ganó a la volatilidad **y** al bootstrap con 30+ resultados |
| Enviar | Aprobada hace menos de 2 min; el cuerpo enviado es **exactamente** el aprobado; se sella antes de salir |
| Siempre | Al leer el capital, si la pérdida del día llega al 5%, la mesa se **apaga sola**. Reactivarla exige escribir `APAGADO OFF` |

Los límites están en `desk/policy.json`, que es tu decisión. Por encima hay
**topes en el código** que ninguna edición de la política puede pasar:
margen 50%, apalancamiento 20x, pérdida diaria 10% y stop-loss siempre
obligatorio. Las guardas impiden que un agente apruebe, envíe, reactive la
mesa, edite la política o el ledger, o toque las claves.

## Otro exchange con ccxt

Por defecto la mesa usa su cliente nativo de BloFin. Para pasar por ccxt,
añade a `desk/policy.json`, tú mismo:

```json
"exchange": {"id": "blofin", "client": "ccxt"}
```

**Hoy solo BloFin está habilitado.** ccxt descarta en silencio el `stopLoss` en
los exchanges que no lo soportan dentro de la orden (en ccxt 4.5.92: Binance,
Gate, HTX, MEXC y Kraken, entre otros), y la mesa nunca envía una orden sin su
stop. Para habilitar otro exchange hay que añadir una prueba como
`test_real_ccxt_blofin_sends_the_same_body_as_the_native_client`, que demuestre
con el ccxt real que el stop viaja en la orden, y sumarlo a
`VERIFIED_EXCHANGES`. Esa decisión es tuya.

- Los instrumentos son los ids del exchange; la mesa exige un único perpetuo
  lineal con ese id.
- Si ccxt fuera a redondear el tamaño, el stop o el objetivo al enviarlos, la
  mesa no envía nada y sella el rechazo: lo que sale es lo aprobado.
- Una respuesta vacía, mal formada o sin id de orden se sella como resultado
  **desconocido**, igual que un corte de red: búscala en el exchange antes de
  proponer otra.
- Claves en la terminal: `CALIOPE_<EXCHANGE>_API_KEY`, `..._API_SECRET` y
  `..._API_PASSWORD` (BloFin sigue con sus `BLOFIN_API_*`).
- En `demo`, la mesa activa el modo sandbox de ccxt; si ese exchange no tiene
  demo en ccxt, se niega. El dinero real sigue pidiendo las dos llaves.
- Las compuertas, tu `APRUEBO` en la terminal y el cuerpo sellado no cambian.
  Para BloFin, una prueba con el ccxt real comprueba que el cuerpo que envía
  ccxt es el mismo del cliente nativo; ccxt solo añade su `brokerId` (etiqueta
  de referido de ccxt).
- El capital se lee de `fetch_balance` (total en USDT). Puede no incluir la
  ganancia o pérdida no realizada igual que `totalEquity` de BloFin: revísalo
  en demo antes de confiar en el límite de pérdida diaria.

## Etapas

1. **Demo** (por defecto): BloFin Demo Trading, dinero ficticio y API real.
   Aquí se prueba todo el circuito.
2. **Real con tu aprobación**: cambias `"stage": "live"` en la política
   **y** pones `CALIOPE_DESK_LIVE=si` en esa terminal. Sin las dos llaves, la
   mesa se niega.
3. **Automático para autores probados**: `"auto": {"enabled": true, …}`. Aun
   así, solo para quien le ganó a los rivales con 30 o más resultados. El
   resto sigue esperando tu aprobación.

## Puesta en marcha (en tu PC)

1. En BloFin → API Management (primero en **Demo Trading**), crea una clave
   con permisos **READ y TRADE**, **sin TRANSFER**, atada a tu IP.
2. En la terminal donde vas a operar (solo en esa sesión, nunca en archivos
   del repo):
   ```powershell
   $env:BLOFIN_API_KEY = "..."
   $env:BLOFIN_API_SECRET = "..."
   $env:BLOFIN_API_PASSPHRASE = "..."
   ```
3. Circuito completo:
   ```powershell
   python -m desk equity
   python -m desk propose --author nathan --question q-... --side buy `
     --margin 0.1 --leverage 3 --sl 79000 --tp 83000 --rationale "rebote en 80k"
   python -m desk approve --order o-...     # te muestra la orden y pide la frase
   python -m desk submit --order o-...      # antes de 2 minutos
   python -m desk status
   python -m desk kill --reason "pausa"     # apagado manual
   ```
   La mesa usa el modo de posición `net` (una sola dirección). Si tu cuenta
   está en modo cobertura, cambia `"position_mode": "hedge"` en la política.

## Qué no hace

- No elige operaciones: las propone un autor a partir de su pronóstico.
- No guarda ni muestra tus claves; las lee del entorno al llamar a la API.
- No retira ni transfiere fondos. El cliente nativo no tiene código para eso.
  ccxt sí trae `withdraw` y `transfer`, pero la mesa solo lo usa a través de una
  lista cerrada de métodos (`desk/ccxt_client.py`, `ALLOWED_METHODS`), y una
  prueba comprueba que ningún archivo de `desk/` llama a un método prohibido.
  La primera defensa sigue siendo la clave: sin permiso de retiro ni
  transferencia.

## Verificado y sin verificar

- Verificado aquí, con un exchange falso: la firma contra la fórmula del SDK
  oficial (blofin-sdk-python) y de ccxt; cada compuerta; el circuito completo;
  la detección de un envío manipulado aunque la cadena se rehaga; las dos
  llaves del modo real; y las guardas para agentes.
- **Sin verificar: la API real de BloFin.** El entorno del agente no llega a
  BloFin. Por eso se empieza en Demo: la primera orden demo confirma la
  firma, los nombres de los campos y el tamaño de contrato. Si BloFin
  responde un error, la mesa lo sella en `result` y no reintenta sola.
