# Themis ⚖️📊

Orquestador multi-agente para **abogados e investigadores financieros**. Usa cualquier IA (gratuita, de pago o local) a través de una sola capa, elige el modelo según la tarea, el costo y la **confidencialidad**, y verifica el resultado antes de entregarlo.

## Qué lo diferencia de un orquestador genérico

| Necesidad profesional | Cómo lo resuelve Themis |
|---|---|
| Secreto profesional / datos de clientes | `sensitivity: confidential` (por defecto) solo permite modelos **locales** (Ollama, LM Studio) o APIs de pago con `no_train`. Los niveles gratuitos que pueden entrenar con tus datos quedan **bloqueados** para material confidencial. |
| Nombres, cédulas, RUC, IBAN, tarjetas | Redacción reversible (`privacy.py`): el modelo ve `[PARTE_1]`, tú ves el nombre real. |
| Alucinación de leyes y sentencias | Reglas anti-invención en cada agente, agente **verificador** adversarial y marca `NO VERIFICADO`. |
| Segunda opinión | `consensus`: varios modelos de proveedores distintos + juez que resalta contradicciones. |
| Trazabilidad | Bitácora JSONL con cadena de hashes (`verify-log` detecta alteraciones). |
| Costo | Modo `free_first` / `balanced` / `quality_first` y tope `max_cost_usd_per_run`. |
| Fuentes | SEC EDGAR (texto completo y XBRL), búsqueda web, lectura de PDF/DOCX del expediente. |

## Verificación mecánica (no confía en lo que dicen los agentes)

Antes de entregar, `verify.py` recalcula y contrasta el informe contra el **libro de evidencia** (todo lo que las herramientas devolvieron, con hash):

- **Procedencia:** toda URL, nº de proceso y artículo citado debe aparecer en una fuente realmente consultada; si no, se bloquea y se obliga a corregir.
- **Aritmética:** se re-evalúan las operaciones escritas en el informe (`a + b = c`).
- **Cifras sin origen:** se marcan números grandes que no vienen de fuentes ni de la solicitud.
- **Enlaces vivos:** se comprueba que las URLs citadas respondan.
- **Reflexión y replanificación:** el verificador revisa señales, cobertura y consistencia entre agentes; las brechas se asignan a un agente y se re-ejecutan.
- **Investigación adversarial:** un agente busca activamente autoridad contraria para evitar el sesgo de confirmación.

## Arquitectura

```
Tarea ─► Planner ─► DAG de agentes en paralelo ─► Verifier ─► Synthesizer ─► informe.md
                     ├ legal_researcher
                     ├ contract_analyst         Router: necesidades + costo + privacidad
                     ├ financial_analyst        ├ Ollama / LM Studio (local)
                     ├ forensic_reviewer        ├ Groq, Cerebras, Gemini, OpenRouter, Mistral (gratis)
                     └ drafter                  └ OpenAI, Anthropic, DeepSeek, Gemini Pro (pago)
```

Cualquier API compatible con OpenAI se agrega con 6 líneas en `config.yaml`.

## Instalación

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...   # las que tengas; ninguna es obligatoria
export GROQ_API_KEY=...
export GEMINI_API_KEY=...
export THEMIS_UA="Tu Nombre tu@correo.com"   # SEC EDGAR exige identificarte
# Para material confidencial sin pagar: instala Ollama y `ollama pull llama3.3:70b`
python -m themis models      # descubre los modelos reales de tus cuentas y ajusta config.yaml
```

## Uso

```bash
# Investigación jurídica (confidencial por defecto → solo local / no_train)
python -m themis run "¿Procede la nulidad de la cláusula 7 por abusiva? Jurisdicción: Ecuador" \
    --files contrato.pdf --protect "Acme S.A." "Juan Pérez"

# Análisis financiero con datos públicos, priorizando modelos gratuitos
python -m themis run "Analiza liquidez y apalancamiento de CIK 0000320193 últimos 8 trimestres" \
    --domain finance --sensitivity public --mode free_first

# Segunda opinión entre modelos distintos
python -m themis consensus "¿Es válido un pacto de no competencia de 5 años?" -n 3 --sensitivity public

# Integridad de la bitácora
python -m themis verify-log
```

Los informes quedan en `runs/informe_*.md` y la auditoría en `runs/audit.jsonl`.

## Notas importantes

- Los IDs de modelo y precios en `config.yaml` son **valores iniciales**: cambian con frecuencia. Verifícalos con `python -m themis models` y los sitios de cada proveedor.
- Revisa los términos de privacidad vigentes de cada proveedor y ajusta `privacy:` según tu plan (p. ej., una cuenta con facturación puede cambiar el tratamiento de datos).
- La redacción por regex reduce, no elimina, el riesgo. Para expedientes muy sensibles usa solo modelos locales.
- Themis es una herramienta de apoyo: toda cita y cifra debe ser verificada por un profesional antes de usarse.

## Ideas para extender

Conectores a bases jurídicas licenciadas (vLex, Westlaw, Lexis) o a datos de mercado (Bloomberg, Refinitiv) como nuevas entradas en `tools.py`; memoria por expediente (SQLite + embeddings locales); exportación a .docx con control de cambios.
