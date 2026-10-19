# Integración futura de IA física

CALIOPE puede coordinar sensores, simuladores y robots mediante adaptadores, pero
la capa física debe permanecer separada de los modelos generativos.

## Flujo autorizado

1. Un adaptador solo lectura normaliza telemetría con marca de tiempo y origen.
2. Los agentes producen una propuesta estructurada, nunca una orden directa.
3. Un verificador determinista comprueba límites, estado y permisos.
4. Un simulador o gemelo digital ejecuta primero la propuesta.
5. La acción real requiere una política explícita y, para acciones materiales,
   aprobación humana en el último paso.
6. Toda transición queda en una bitácora inmutable y reproducible.

## Límites iniciales

- Sin credenciales dentro de prompts, logs o repositorio.
- Sin acceso arbitrario al sistema de archivos ni a URLs internas.
- Sin ejecución de shell generada por modelos.
- Allowlist de dispositivos, comandos, magnitudes y frecuencias.
- Parada segura independiente del orquestador y del proveedor de IA.
- Trading y control físico se mantienen en dominios separados.

La primera etapa de CALIOPE es análisis y validación. Los adaptadores físicos se
habilitarán después de disponer de esquemas, simulación, pruebas y controles.
