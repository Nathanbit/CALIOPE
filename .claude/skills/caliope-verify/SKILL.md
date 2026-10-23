---
name: caliope-verify
description: Verify a change to CALIOPE before declaring it done, committing or opening a PR. Runs only the checks the changed files require (doctor, root unittest, archived tests) and reports what remains unverified. Use when the person says "verifica", "¿está listo?", "corre las pruebas", "antes del PR", or after finishing any code change.
---

# Verificación de CALIOPE

Adaptada del ciclo *verification-loop* de ECC (affaan-m/ECC, MIT).

1. Ejecuta desde la raíz del repo:

   ```bash
   python tools/verify.py            # cambios sin commit
   python tools/verify.py --base main  # rama completa antes de un PR
   ```

   Usa `--all` si la persona pide la batería entera.

2. Si algo falla, lee solo las últimas líneas que imprime el script, arregla la
   causa en el dueño del formato y vuelve a ejecutar. No desactives ni
   reescribas una prueba para que pase sin explicar por qué la prueba estaba
   mal.

3. Informa en pocas líneas: qué pasó, qué falló y se arregló, y copia tal cual
   cada línea `NO VERIFICADO` y cada `SKIP`. No digas "todo verificado" si hay
   alguna.

4. Para un cambio grande o sensible, pide además una revisión en contexto
   limpio con el agente `caliope-reviewer` (y `caliope-security-reviewer` si
   toca puente, brokers, red, hooks o dependencias).
