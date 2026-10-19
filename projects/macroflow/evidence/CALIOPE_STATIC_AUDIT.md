# Auditoría estática de `CALIOPE.zip`

Fecha: 2026-10-18  
Fuente inspeccionada: `C:\Users\natha\Downloads\THEMIS\CALIOPE.zip`  
SHA-256: `F7AA9F1A038B4C3E83444CC428C298833094CF5B7F7067E4FBAFF39860DB10F6`  
Tamaño: 25 100 bytes

La revisión fue estática: se leyó el índice del ZIP y su contenido textual sin ejecutar el paquete.

## Identidad y estado del proyecto

El archivo se llama `CALIOPE.zip`, pero el proyecto interno se presenta como **Themis** y el paquete de Python también se llama `themis`. El ZIP no contiene metadatos Git, pruebas, licencia, `pyproject.toml`, archivo de bloqueo ni CI. Por sí solo no es un repositorio reproducible.

El repositorio de MacroFlow existente está inicializado en `macroflow/.git`, pero al momento de esta revisión no tenía commits ni remoto y todo su contenido aparecía sin seguimiento. Antes de integrar código conviene crear un primer commit limpio, excluir temporales y separar código, evidencia y resultados.

## Estructura observada

```text
themis/
├── README.md
├── config.yaml
├── requirements.txt
└── themis/
    ├── __init__.py
    ├── __main__.py
    ├── agents.py
    ├── audit.py
    ├── cli.py
    ├── orchestrator.py
    ├── privacy.py
    ├── providers.py
    ├── router.py
    ├── tools.py
    └── verify.py
```

## Tecnologías y propósito

- Python asíncrono con `asyncio`.
- `httpx` para proveedores LLM, búsqueda, páginas web y SEC EDGAR.
- `PyYAML` para política, modelos, costes y privacidad.
- SQLite para caché local.
- `pypdf` y `python-docx` como lectores opcionales.
- DAG de agentes planificado por un LLM, ejecución paralela, verificador, replanificación y síntesis.
- Router entre proveedores locales y remotos según capacidades, coste y sensibilidad.
- Redacción reversible por expresiones regulares.
- Libro de evidencia con hashes y bitácora JSONL encadenada.
- Compuerta mecánica limitada para procedencia de URLs/artículos, aritmética, cifras y enlaces vivos.

## Componentes reutilizables para MacroFlow

1. **DAG por responsabilidades.** Permite separar la revisión Pine, ejecución/riesgo, validación estadística, contexto de mercado y auditoría adversarial.
2. **Ejecución paralela.** Las IAs pueden revisar aspectos independientes y entregar salidas comparables.
3. **Libro de evidencia.** Puede registrar script, hash, commit, configuración, captura, métricas y fuentes de mercado usadas en cada experimento.
4. **Verificación adversarial.** Un agente distinto puede buscar repinte, lookahead, selección retrospectiva, mezcla de planes, errores de tamaño y costes omitidos.
5. **Presupuesto y enrutamiento.** Sirve para asignar modelos costosos solo a problemas que lo justifican.
6. **Consenso.** Es útil para encontrar contradicciones, pero no debe convertirse en señal de trading ni contar votos como evidencia estadística.

## Riesgos y limitaciones

### Prioridad alta

- `read_file(path)` acepta cualquier ruta accesible. Un documento o prompt malicioso podría inducir a un agente a leer archivos ajenos al proyecto y enviar su contenido a un proveedor.
- `fetch_url(url)` acepta cualquier URL. Sin una lista de dominios/protocolos y bloqueo de IP privadas existe riesgo de SSRF y acceso a servicios locales.
- Las etiquetas `local`, `no_train` y `may_train`, así como IDs y precios de modelos, son configuración declarativa; el código no comprueba contratos ni políticas vigentes.
- No existe validación estructural de argumentos de herramientas ni de las respuestas de agentes. Se extrae el primer bloque JSON con una expresión regular codiciosa.
- No hay pruebas automatizadas ni fixtures que demuestren que la privacidad, el presupuesto, la compuerta y el DAG se comportan como se afirma.

### Prioridad media

- El límite de coste se comprueba antes de cada llamada, de modo que una sola llamada puede superar el presupuesto restante.
- Las dependencias solo tienen límites inferiores y no están fijadas por hash; las ejecuciones no son reproducibles.
- La caché SQLite guarda respuestas localmente y necesita reglas de retención, cifrado o desactivación para material sensible.
- La cadena de hashes detecta cambios casuales, pero no está firmada: quien pueda reescribir todo el archivo puede recalcular la cadena.
- Si el DAG contiene ciclos o dependencias rotas, el orquestador ejecuta los pasos pendientes de todos modos.
- La compuerta verifica presencia, no que una fuente respalde semánticamente una afirmación. Los problemas de severidad media no impiden aprobar.
- Las descargas se truncan después de cargarse; falta limitar tamaño y tipo antes de leer la respuesta completa.

### Observaciones estáticas

- No se encontraron llamadas a `subprocess`, shell, persistencia del sistema, borrado recursivo ni ejecución de binarios.
- `calc` usa `eval`, aunque una expresión regular muy restrictiva y `__builtins__` vacío reducen considerablemente el riesgo de ejecución arbitraria.
- Este resultado no equivale a una auditoría de seguridad exhaustiva ni demuestra que las dependencias o servicios externos sean seguros.

## Arquitectura propuesta para la revisión multi-IA de MacroFlow

Mantener Pine y la orquestación separados. Pine sigue siendo la fuente determinista de señales y telemetría; la capa Python solo revisa artefactos y evidencia, inicialmente sin enviar órdenes.

```text
manifest del experimento
        │
        ├── pine_reviewer          sintaxis v6, estado, causalidad, MTF y no-repaint
        ├── execution_risk         stop, lotaje, costes, fills y límites diarios
        ├── statistical_validator  N por plan, MFE/MAE, walk-forward, purga y holdout
        ├── market_context         Asia, sesiones, feriados y fuentes punto-en-tiempo
        └── adversarial_reviewer   sobreajuste, leakage y escenarios de fallo
                    │
               mechanical gates
                    │
                synthesizer
                    │
       decisión: aceptar / rechazar / experimento
```

Cada agente debe devolver JSON con esquema fijo, evidencia y nivel de confianza. El sintetizador no debe poder cambiar métricas ni agregar fuentes. Una salida LLM nunca autoriza una orden.

## Contrato mínimo por experimento

- `model_version`, `experiment_id` y commit Git.
- SHA-256 del Pine y de todos los datos exportados.
- símbolo, mercado, temporalidad, zona horaria y rango cronológico.
- configuración completa del Strategy Tester, comisiones, slippage y tamaño.
- métricas por plan completo: N, P&L, PF, drawdown, MFE, MAE, duración y exposición.
- particiones `train`, `validation` y `holdout`, con embargo temporal.
- fuentes de contexto publicadas antes de cada decisión.
- hallazgos de cada agente, contradicciones y compuertas aprobadas/rechazadas.

## Cambios previos a cualquier ejecución

1. Aplicar allowlist de raíces para archivos y rechazar rutas resueltas fuera de ellas.
2. Permitir solo HTTPS y dominios aprobados; resolver DNS y bloquear loopback, link-local e IP privadas.
3. Validar herramientas y respuestas mediante JSON Schema o Pydantic.
4. Crear pruebas para router, presupuesto, redacción, evidencia, auditoría y DAG.
5. Fijar dependencias y añadir licencia, `pyproject.toml`, CI y análisis estático.
6. Añadir agentes y herramientas específicas de MacroFlow sin habilitar ejecución de órdenes.
7. Registrar todo resultado por commit y separar `src/`, `tests/`, `experiments/`, `reports/` y `artifacts/`.

## Organización Git sugerida

```text
macroflow/
├── src/pine/                 # versiones publicables
├── src/orchestrator/         # adaptación endurecida de Themis/Caliope
├── tests/                    # pruebas Python y fixtures Pine
├── experiments/<id>/         # manifest y configuración inmutables
├── reports/                  # auditorías humanas
├── artifacts/                # capturas y exportaciones; preferir Git LFS
├── archive/                  # versiones históricas
├── pyproject.toml
├── requirements.lock
├── .gitignore
└── README.md
```

La primera integración debe conservar el ZIP original como fuente de procedencia y registrar su hash, pero no mezclar el paquete sin endurecer con el flujo que maneje cuentas, claves o ejecución de operaciones.
