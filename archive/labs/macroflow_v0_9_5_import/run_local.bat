@echo off
REM MacroFlow: doble clic para instalar (la primera vez) y abrir el panel en http://localhost:8000
cd /d "%~dp0"
where python >nul 2>nul || (echo Necesitas Python 3.11+ de https://www.python.org/downloads/ ^(marca "Add to PATH"^) & pause & exit /b 1)
if not exist .venv (
  echo Creando entorno e instalando dependencias, tarda un par de minutos la primera vez...
  python -m venv .venv || (pause & exit /b 1)
  .venv\Scripts\python -m pip install -q --upgrade pip
  .venv\Scripts\python -m pip install -q -e ".[dev]" || (pause & exit /b 1)
)
.venv\Scripts\python scripts\serve.py
pause
