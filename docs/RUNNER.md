# Runner de revisión por modelos (`caliope_loop.py`)

Detalle técnico de la pieza de gobierno. El resumen está en el `README.md`.

## Primer flujo real

`workflows/macroflow-review.yaml` organiza la revisión de MacroFlow mediante
cinco roles separados: causalidad Pine, riesgo/ejecución, estadística, contexto
de mercado y red-team. El sintetizador solo recibe propuestas que superen sus
criterios de aceptación.

## Mejora recursiva

`workflows/macroflow-recursive-improvement.yaml` especifica el ciclo acotado de
automejora. Perplexity, DeepSeek, Qwen, Z.ai, Kimi, Gemini, Meta y NVIDIA
Nemotron revisan ámbitos distintos; Claude/Themis organiza la evidencia sin
votar ni saltarse compuertas. Los contratos JSON están en `contracts/` y el
diseño completo en `docs/RECURSIVE_MACROFLOW_LOOP.md`.

`recursive_evidence.py` añade el registro empírico por operación y cartera. El
ledger enlaza cada evento mediante SHA-256 y separa cinco estados:
observación, hipótesis, candidato, validación y promoción humana. Los casos
abiertos se conservan, pero no entrenan el ciclo. Para avanzar se exigen PnL,
MFE, MAE, slippage, sensibilidad a gaps, validación de concentración y varias
muestras selladas OOS/holdout. Una validación fallida no puede omitirse de una
promoción. El contrato y el procedimiento están documentados en
`docs/PORTFOLIO_RECURSIVE_EVIDENCE.md`.

## Runner local

`caliope_loop.py` implementa la parte determinista del ciclo. Conserva snapshots
inmutables del baseline y los prompts, genera ocho paquetes de revisión, valida
cada respuesta, contabiliza el presupuesto y controla la transición entre
estados. En el estado actual permite `reject` y `revise`; bloquea
`human_review` hasta que un futuro builder registre un candidato verificable.

Ejecuta los comandos desde la carpeta `CALIOPE` con Python 3.11 o posterior:

```powershell
# 1. Crear un expediente y los ocho paquetes en runs/<run-id>/requests/
python caliope_loop.py init `
  --baseline projects/macroflow/source/MacroFlow_Backtest_v0_9_4_VALIDATION.pine `
  --prompts projects/macroflow/evidence/AI_REVIEW_PROMPTS_v095.md `
  --run-id mf-v095-consolidation-20261019

# 2. Consultar su estado
python caliope_loop.py status --run-id mf-v095-consolidation-20261019

# 3. Ingerir una respuesta JSON; repetir una vez por cada revisor
python caliope_loop.py ingest `
  --run-id mf-v095-consolidation-20261019 `
  --agent qwen `
  --file C:/ruta/a/qwen-response.json

# 4. Ingerir la decisión cuando estén recibidas las ocho respuestas
python caliope_loop.py decision `
  --run-id mf-v095-consolidation-20261019 `
  --file C:/ruta/a/decision.json
```

`init` no envía solicitudes. Solo produce los archivos que un adaptador o una
persona debe entregar a cada modelo. `ingest` tampoco acepta texto libre: el
archivo debe cumplir el contrato del rol, conservar `run_id`, iteración y hashes,
y no exceder los límites. `decision` reconoce `reject`, `revise` y
`human_review`, pero esta última falla cerrada mientras no exista un candidato
registrado por el builder. Nunca publica ni activa una estrategia.

Prueba local del runner:

```powershell
python -m unittest discover -s tests -p "test_caliope_loop.py" -v
```

Para comprobar si un revisor es realmente determinista, `replay` compara una
segunda captura con la original (ver `docs/DETERMINISM.md`).

## Ocho revisores externos

Cada run crea un paquete separado para estos revisores. Sus opiniones son
evidencia; ninguna tiene autoridad para promover un cambio.

| Identificador del runner | Función principal | Respuesta |
|---|---|---|
| `perplexity` | Fuentes, sesiones, calendario y contexto de mercado | observación |
| `deepseek` | Riesgo, ejecución, sizing, costes y salidas | observación |
| `qwen` | Pine v6, causalidad, MTF, estados y compilación | observación |
| `zai` | Red-team de regímenes adversos y sobreajuste | observación |
| `kimi` | Walk-forward, bootstrap, selección y holdout | observación |
| `gemini` | Seguridad, separación research/paper/live y caos | observación |
| `meta` | Máquina de estados, concurrencia, invariantes y rollback | observación |
| `nvidia_nemotron` | DAG, presupuestos, guardrails y cómputo apto para GPU | observación |

Claude/Themis puede ordenar la evidencia y preparar una síntesis, pero no figura
como noveno voto ni puede saltarse los gates. Los verificadores deterministas
tienen prioridad sobre cualquier consenso entre modelos.

### Primera consolidación completada

El run `mf-v095-consolidation-20261019` recibió las ocho observaciones y pasó a
`evidence_merge` con 8 llamadas, 6.440 tokens de entrada, 3.330 de salida y coste
declarado USD 0. Claude sintetizó la evidencia, conservó los desacuerdos y
rechazó la promoción del ZIP v0.9.5. El informe público está en
`projects/macroflow/reviews/AI_CONSOLIDATED_REVIEW_2026-10-19.md` y su índice de
respuestas en `AI_REVIEW_EVIDENCE_INDEX_2026-10-19.json`.

La decisión `reject` cerró el run en `archived_rejected` y mantiene el
artefacto en cuarentena. No hay candidata, ejecución automática ni evidencia de
rentabilidad.

## Límites y fallo cerrado

Cada manifest fija actualmente estos topes globales: dos iteraciones, dos
candidatos, 16 respuestas, 60,000 tokens de entrada, 12,000 de salida y USD 2.00
de coste declarado. El runner rechaza una entrada y devuelve código 2 si detecta,
entre otros casos:

- campos requeridos ausentes o campos no permitidos;
- `run_id`, iteración, agente o tipo de respuesta inconsistentes;
- hashes inválidos, parche cuyo contenido no coincide con su SHA-256 o evidencia
  repetida;
- una segunda respuesta del mismo agente, una propuesta/parche emitido por un
  revisor, una carrera concurrente o un presupuesto excedido;
- una decisión anticipada, sin gates, sin razones o sin declarar que exige
  aprobación humana.

La segunda iteración solo se abre mediante una decisión `revise` y dentro del
límite. `human_review` solo podrá detener el runner cuando un futuro builder
haya materializado y registrado el candidato que se pretende revisar.
El runner no implementa todavía un comando de aprobación, merge, publicación,
paper trading ni trading real. `automatic_publish` y `automatic_trading`
permanecen en `false`.

## Adaptadores pendientes

Los adaptadores de navegador y API aún requieren intervención y autorización.
CALIOPE no controla por sí solo las ventanas abiertas de Perplexity, DeepSeek,
Qwen, Z.ai, Kimi, Gemini, Meta o NVIDIA, ni posee sus credenciales. Por ahora una
persona o un orquestador autorizado debe:

1. abrir el paquete de `runs/<run-id>/requests/`;
2. enviarlo al proveedor correspondiente;
3. guardar únicamente el JSON contractual devuelto;
4. ejecutarlo mediante `ingest`.

Una autorización para consultar un modelo no autoriza escribir en repositorios,
publicar, manejar secretos ni operar un broker. Los futuros adaptadores deberán
conservar esa separación, aplicar timeouts y presupuestos y fallar cerrados ante
un proveedor ausente, una respuesta inválida o una autorización insuficiente.

El motor heredado `archive/themis/` y los workflows describen capacidades adicionales,
pero no deben confundirse con garantías ya implementadas por el runner local.
