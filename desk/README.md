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
- No retira ni transfiere fondos: no existe código para eso.

## Verificado y sin verificar

- Verificado aquí, con un exchange falso: la firma contra la fórmula del SDK
  oficial (blofin-sdk-python) y de ccxt; cada compuerta; el circuito completo;
  la detección de un envío manipulado aunque la cadena se rehaga; las dos
  llaves del modo real; y las guardas para agentes.
- **Sin verificar: la API real de BloFin.** El entorno del agente no llega a
  BloFin. Por eso se empieza en Demo: la primera orden demo confirma la
  firma, los nombres de los campos y el tamaño de contrato. Si BloFin
  responde un error, la mesa lo sella en `result` y no reintenta sola.
