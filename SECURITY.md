# Seguridad

El motor importado todavía no debe ejecutarse con datos sensibles ni acciones
reales. Antes de habilitar proveedores o herramientas se requieren:

- allowlist de rutas dentro del workspace;
- bloqueo de URLs privadas, locales y metadatos de nube;
- validación JSON con esquemas versionados;
- presupuesto duro antes de cada llamada;
- dependencias fijadas y pruebas automatizadas;
- separación entre propuesta, simulación, aprobación y ejecución;
- logs sin secretos y con hashes anclados fuera del propio archivo.

Las claves se cargan únicamente desde el entorno local y nunca se versionan.
