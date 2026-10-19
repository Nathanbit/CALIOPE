import json
import time

from macroflow.config import Settings
from macroflow.optimization.autoloop import AutoLoop, LoopConfig
from macroflow.risk.guards import RiskStateStore


def _loop(tmp_path):
    s = Settings(state_dir=tmp_path / "state", registry_dir=tmp_path / "reg")
    return AutoLoop(s, RiskStateStore(s.risk_state_path)), s


def _wait(loop, timeout=240):
    t0 = time.time()
    while loop._thread.is_alive() and time.time() - t0 < timeout:
        time.sleep(0.1)
    assert not loop._thread.is_alive()


LENIENT = dict(trials=5, min_oos_pf=0.0, require_positive_ci=False, auto_approve=True, interval_s=0.05)


def test_loop_runs_recursively_and_stops_at_max(tmp_path):
    loop, s = _loop(tmp_path)
    loop.start(LoopConfig(max_iterations=3, **LENIENT))
    _wait(loop)
    st = loop.state
    assert st.iterations == 3 and len(st.history) == 3
    assert st.stop_reason.startswith("alcanzado") and not st.loop_active
    # memoria recursiva: el espacio estrechado se guarda y el optimizador es el mismo objeto
    assert loop.space_path.exists()
    assert loop.opt.iteration == 3
    # cada iteración vio datos nuevos (no hay "no_new_data") y alguna promovió versión
    assert all(h["status"] != "no_new_data" for h in st.history)
    assert any(h["version"] for h in st.history)


def test_loop_brakes_and_stops_itself(tmp_path):
    loop, s = _loop(tmp_path)
    loop.start(LoopConfig(max_iterations=1, **LENIENT))
    _wait(loop)
    assert loop.store.load().active_model_version            # hay modelo activo
    loop.start(LoopConfig(max_iterations=5, **{**LENIENT, "min_oos_pf": 99.0}))
    _wait(loop)
    assert loop.state.stop_reason.startswith("FRENO")
    assert loop.store.load().risk_multiplier == 0.0
    assert loop.state.history[-1]["status"] == "BRAKE"


def test_loop_state_survives_restart(tmp_path):
    loop, s = _loop(tmp_path)
    loop.start(LoopConfig(max_iterations=0, **{**LENIENT, "interval_s": 30}))
    while loop.state.iterations < 1:
        time.sleep(0.1)
    loop._stop.set()                                          # simula apagar el servidor sin parar el bucle
    loop._thread.join(10)
    saved = json.loads((s.state_dir / "autoloop.json").read_text())
    assert saved["loop_active"] is True
    again = AutoLoop(s, RiskStateStore(s.risk_state_path))   # "reinicio"
    assert again.state.loop_active and again.state.iterations == 1
