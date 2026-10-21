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

## Cadena de suministro y GitHub (respuesta a GhostAction, octubre 2026)

En octubre de 2026 la campaña GhostAction usó cuentas de GitHub comprometidas
(tokens robados por infostealers) para añadir workflows disfrazados de
"security audit" que exfiltraban secretos de Actions y de la historia de git.
CALIOPE no usa CI, así que su defensa es no ofrecer nada que robar y detectar
cualquier workflow ajeno.

**En el repositorio (automático):**

- `python caliope_loop.py doctor` falla si aparece cualquier archivo en
  `.github/workflows/` que no esté aprobado por hash en
  `security/allowed_workflows.sha256`, o si hay secretos o marcadores de la
  campaña en archivos versionados.
- `python caliope_loop.py scan-secrets [--staged]` busca claves de AWS,
  GitHub, GitLab, Anthropic, OpenAI, OpenRouter, Google, Slack, SendGrid, npm,
  PyPI y claves privadas. Solo informa archivo, línea y tipo; nunca el valor.
- Hook local: `git config core.hooksPath .githooks` (una vez por clon) bloquea
  commits que contengan secretos.

**En la cuenta (manual, lo hace la persona):**

1. 2FA activado en GitHub, con app o llave física.
2. Revisar y revocar tokens: Settings → Developer settings → Personal access
   tokens. Preferir tokens de grano fino, con un solo repo y caducidad corta.
3. Revisar Settings → Applications (OAuth y GitHub Apps autorizadas) y
   Settings → Security log.
4. Proteger `main`: Settings → Branches (o Rules) → exigir pull request,
   bloquear force-push y borrado.
5. Actions: Settings → Actions → General → desactivar Actions o "Allow
   select actions", permisos de workflow en solo lectura y sin aprobar PRs.
6. Mantener activados secret scanning y push protection.
7. Ninguna clave de IA (Anthropic, OpenAI, OpenRouter, etc.) en el repo ni en
   GitHub Actions secrets. Si alguna estuvo expuesta, rotarla en el proveedor:
   borrarla del repo no la invalida.

**Si aparece un workflow que no añadiste:** no lo borres primero. Guarda el
archivo, los logs de ejecución y el commit; revoca el token de la cuenta que lo
añadió; rota todos los secretos que pudo leer; y después elimínalo.
