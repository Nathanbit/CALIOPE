#!/usr/bin/env bash
# MacroFlow: instala (la primera vez) y abre el panel en http://localhost:8000
set -e
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python) || { echo "Necesitas Python 3.11+"; exit 1; }
if [ ! -d .venv ]; then
  echo "Creando entorno e instalando dependencias (un par de minutos la primera vez)..."
  "$PY" -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -e ".[dev]"
fi
exec .venv/bin/python scripts/serve.py
