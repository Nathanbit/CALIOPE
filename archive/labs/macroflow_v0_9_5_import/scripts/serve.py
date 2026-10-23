"""Arranca el puente + panel en http://localhost:8000 y abre el navegador."""
from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import uvicorn  # noqa: E402

PORT = int(os.environ.get("MF_PORT", "8000"))


def _find_brave() -> str | None:
    """Ruta de Brave si está instalado (Windows, macOS, Linux); si no, None."""
    import shutil
    env = os.environ
    candidates = [
        env.get("MF_BROWSER_PATH", ""),
        os.path.join(env.get("PROGRAMFILES", r"C:\Program Files"), r"BraveSoftware\Brave-Browser\Application\brave.exe"),
        os.path.join(env.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), r"BraveSoftware\Brave-Browser\Application\brave.exe"),
        os.path.join(env.get("LOCALAPPDATA", ""), r"BraveSoftware\Brave-Browser\Application\brave.exe"),
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        shutil.which("brave-browser") or "", shutil.which("brave") or "",
    ]
    return next((c for c in candidates if c and os.path.isfile(c)), None)


def _open():
    time.sleep(1.5)
    url = f"http://localhost:{PORT}"
    brave = _find_brave()
    if brave:
        import subprocess
        subprocess.Popen([brave, url])
        print(f"  Abierto en Brave: {url}")
    else:
        webbrowser.open(url)
        print(f"  Brave no encontrado; abierto en el navegador por defecto: {url}")


if __name__ == "__main__":
    print(f"\n  MacroFlow en http://localhost:{PORT}   (Ctrl+C para parar)\n")
    if "--no-browser" not in sys.argv:
        threading.Thread(target=_open, daemon=True).start()
    uvicorn.run("macroflow.bridge.app:app", host="127.0.0.1", port=PORT, log_level="warning")
