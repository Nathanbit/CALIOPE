# La firma en Google ADK (laboratorio)

La firma de CALIOPE (vigía, analistas, oficial de riesgo y la persona en la
mesa) hoy vive en `.claude/agents/` y solo corre dentro de Claude Code. Aquí la
misma firma está armada con [Google ADK](https://adk.dev/get-started/), así que
corre con cualquier modelo (Gemini directo; DeepSeek, Kimi, Qwen y otros por
LiteLLM) y tiene una interfaz web (`adk web`). Cada IA compite en el ledger con
su propio autor (`ia:gemini`, `ia:deepseek`…), así que `rival` y el enjambre
miden por fin a varias IAs a la vez.

## La ronda

```
ronda (SequentialAgent)
├── vigia            due + estado de la mesa → "agenda" (solo lee)
├── analistas        ParallelAgent: una rama por IA, cada una con su autor
│   ├── analista_gemini
│   └── analista_deepseek …
└── riesgo           PROCEDE / NO PROCEDE sobre órdenes pendientes (solo lee)
```

Los analistas corren en ramas separadas: ninguno ve lo que pronosticaron los
otros. Es lo que exigen el enjambre (`~enjambre`) y `rival`, que suponen
pronósticos independientes. Una prueba lo comprueba.

## Lo que el modelo no decide

| Qué | Cómo se garantiza |
|---|---|
| Su autor | Va atado a cada analista al construirlo; no es un argumento de la herramienta. `nathan`, `claude` e `ia:claude` están vetados |
| Sellar en el ledger | `sellar` corre primero `seal --dry-run` (todas las compuertas) y le muestra el resultado a la persona; solo escribe si ella confirma en ADK |
| Proponer una orden | `proponer` pide confirmación en ADK y luego llama a `python -m desk propose`, que vuelve a pasar todas las compuertas. Margen ≤ 10% y apalancamiento ≤ 5x en el laboratorio |
| Aprobar, enviar, rechazar, apagar o reactivar la mesa | **No existe ninguna herramienta para eso.** La aprobación sigue siendo `python -m desk approve` en tu terminal. Una prueba revisa que ninguno de esos comandos aparezca en el código |
| Dinero real | Las herramientas quitan `CALIOPE_DESK_LIVE` del entorno: la firma ADK solo opera en demo |
| Claves de BloFin | Solo llegan al subproceso de la mesa; el resto de herramientas no las recibe |
| Herramientas de otro agente | Un `before_tool_callback` rechaza cualquier llamada fuera de la lista de cada agente |
| Fallos | Cualquier código de salida distinto de cero, timeout o argumento inválido devuelve `ok: false` |

Las CLI siguen siendo las únicas dueñas de sus formatos: las herramientas no
leen ni escriben los ledgers, solo llaman a `prediction_ledger.py`,
`python -m desk` y `tools/fetch_klines.py` con argumentos fijos.

## Puesta en marcha (en tu PC)

```powershell
cd CALIOPE
python -m venv .venv-adk
.venv-adk\Scripts\activate
pip install -r labs/adk_firm/requirements.txt

# Modelos: copia el ejemplo y pon tu clave de Gemini (y las de LiteLLM si usas otros)
copy labs\adk_firm\caliope_firm\.env.example labs\adk_firm\caliope_firm\.env

cd labs/adk_firm
adk web --port 8000        # abre http://localhost:8000 y elige caliope_firm
# o en la terminal:
adk run caliope_firm
```

Escribe "ronda" (o "ronda, y quiero operar si hay señal" para que los
analistas puedan proponer). Cuando un analista quiera sellar o proponer, ADK te
muestra el diálogo de confirmación con lo que va a escribir.

El `.env` es solo para claves de modelos y está ignorado por git. **Nunca
pongas ahí las claves de BloFin**: van en la terminal, como dice
`desk/README.md`.

## Verificado y sin verificar

- Verificado aquí, sin red y con un modelo falso (`tests/test_adk_firm.py`, 14
  pruebas): la frontera de herramientas; que ningún analista ve a otro; que
  sellar se queda en seco hasta que la persona confirma; que confirmar escribe
  una sola vez y rechazar no escribe nada; el veto de autores; el bloqueo de
  herramientas ajenas; y que las claves y el interruptor de dinero real no
  llegan donde no deben. Con google-adk 2.13.0.
- **Sin verificar:** modelos reales (aquí no hay claves), la interfaz `adk web`
  con sus diálogos de confirmación, y cómo se portan los modelos con estas
  instrucciones. La primera ronda real conviene hacerla en demo y mirando cada
  confirmación.
- ADK 2.x marca `SequentialAgent` y `ParallelAgent` como obsoletos en favor de
  `Workflow` (flujos de grafo), que aún no admite agentes LLM como subagentes.
  Cuando lo haga, conviene migrar la ronda.
- La confirmación de herramientas de ADK es experimental y no funciona con
  `DatabaseSessionService` ni `VertexAiSessionService`: usa la sesión en
  memoria que traen `adk web` y `adk run`.
