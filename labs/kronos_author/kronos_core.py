"""Kronos as a CALIOPE author: the model-free part. Standard library only.

Kronos (shiyu-coder/Kronos, MIT) samples future candles. Its ``predict``
averages the sampled paths (``np.mean(preds, axis=1)`` in model/kronos.py),
and an averaged path is far smoother than any real one: it would rarely
"touch" a level. So paths are drawn one by one (``sample_count=1``) and each
is judged with ``resolver.step_outcome``, the rule that resolves real
candles. The probability is the share of paths where the question comes
true, void paths left out, as in scoring.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import resolver  # noqa: E402

MAX_PRED_LEN = 1000
Path_ = Sequence[tuple[float, float, float, float]]  # (open, high, low, close) per future candle


class KronosAuthorError(ValueError):
    pass


def history_and_future(rule: dict, horizon_end: str, data: dict, now_ms: int,
                       lookback: int) -> tuple[list[dict], list[int]]:
    """Closed candles before ``now`` (at most ``lookback``, contiguous, ending
    in the last candle closed by ``now``) and the open times to predict,
    from the next candle up to ``horizon_end``."""
    rule = resolver.validate_rule(rule, horizon_end)
    if data.get("interval") != rule["interval"] or str(data.get("symbol", "")).upper() != rule["symbol"]:
        raise KronosAuthorError("the candles must have the question's symbol and interval")
    step = resolver.INTERVAL_MS[rule["interval"]]
    start, end = resolver.to_ms(rule["start"], "start"), resolver.to_ms(horizon_end, "horizon_end")
    if now_ms >= start:
        raise KronosAuthorError("the window has already started: a forecast now would see it")
    rows = {}
    for row in data.get("klines", []):
        candle, close_ms = resolver._parse_row(row)  # noqa: SLF001 (shared row checks)
        if close_ms < now_ms and close_ms == candle["open_ms"] + step - 1:
            rows[candle["open_ms"]] = candle
    if not rows:
        raise KronosAuthorError("no closed candles before now")
    last = max(rows)
    if now_ms - (last + step) >= step:
        raise KronosAuthorError("the candles end too long before now: fetch them again")
    history = []
    t = last
    while t in rows and len(history) < lookback:
        history.append(rows[t])
        t -= step
    history.reverse()
    if len(history) < min(lookback, 64):
        raise KronosAuthorError(f"only {len(history)} contiguous candles before now")
    future = list(range(last + step, end, step))
    if len(future) > MAX_PRED_LEN:
        raise KronosAuthorError(f"{len(future)} candles to predict: more than {MAX_PRED_LEN}")
    return history, future


def judge_path(rule: dict, future: list[int], path: Path_, start_ms: int) -> str:
    """Outcome of one sampled path, looking only at candles inside the
    window. Candles are made consistent (high = max, low = min of the four
    prices) because a sampled candle may not be."""
    if len(path) != len(future):
        raise KronosAuthorError("a path does not have one candle per future time")
    for t, (o, h, lo, c) in zip(future, path):
        if t < start_ms:
            continue
        outcome = resolver.step_outcome(rule, max(o, h, lo, c), min(o, h, lo, c), c)
        if outcome is not None:
            return outcome
    return resolver.undecided_outcome(rule)


def draw(future: list[int], sample: Callable[[int], list[Path_]], paths: int, batch: int) -> list[Path_]:
    """``paths`` independent paths, in batches, each with one candle per future time."""
    if paths < 1 or batch < 1:
        raise KronosAuthorError("paths and batch must be at least 1")
    out: list[Path_] = []
    while len(out) < paths:
        size = min(batch, paths - len(out))
        got = sample(size)
        if len(got) != size:
            raise KronosAuthorError("the model returned a different number of paths")
        for path in got:
            if len(path) != len(future):
                raise KronosAuthorError("a path does not have one candle per future time")
        out.extend(got)
    return out


def count(rule: dict, future: list[int], drawn: list[Path_]) -> dict:
    """How many paths make a validated yes/no rule true, false or void."""
    if rule["kind"] == "close_value":
        raise KronosAuthorError("a value question is answered with quantiles, not a probability")
    start = resolver.to_ms(rule["start"], "start")
    counts = {"true": 0, "false": 0, "void": 0}
    for path in drawn:
        counts[judge_path(rule, future, path, start)] += 1
    decided = counts["true"] + counts["false"]
    if not decided:
        raise KronosAuthorError("every path was void: no probability")
    return {"probability": round(counts["true"] / decided, 4), "paths": len(drawn), **counts}


def probability(rule: dict, horizon_end: str, future: list[int], sample: Callable[[int], list[Path_]],
                paths: int, batch: int) -> dict:
    """Draw ``paths`` independent paths in batches and count outcomes."""
    rule = resolver.validate_rule(rule, horizon_end)
    return count(rule, future, draw(future, sample, paths, batch))


def _quantile(sorted_values: list[float], level: float) -> float:
    """Linear interpolation between order statistics (numpy's default, type 7)."""
    pos = level * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def final_close_quantiles(drawn: list[Path_], levels: Sequence[str] = resolver.QUANTILE_LEVELS) -> dict:
    """Quantiles of the last close across paths: the fan at the horizon."""
    if len(drawn) < 20:
        raise KronosAuthorError("fewer than 20 paths cannot give p10 and p90")
    closes = sorted(float(path[-1][3]) for path in drawn)
    return {level: round(_quantile(closes, float(level)), 2) for level in levels}


def value_quantiles(rule: dict, horizon_end: str, future: list[int], sample: Callable[[int], list[Path_]],
                    paths: int, batch: int) -> tuple[dict, list[Path_]]:
    """A close_value question: quantiles of the close of the window's last candle."""
    rule = resolver.validate_rule(rule, horizon_end)
    if rule["kind"] != "close_value":
        raise KronosAuthorError("quantiles only answer value (close_value) questions")
    drawn = draw(future, sample, paths, batch)
    return final_close_quantiles(drawn), drawn


# --- replay: does the fan contain what happened? ------------------------------

def closed_candles(data: dict) -> list[dict]:
    """Every closed candle in the file, sorted, with its interval checked."""
    step = resolver.INTERVAL_MS.get(data.get("interval"))
    if step is None:
        raise KronosAuthorError("the candles file has no known interval")
    rows: dict[int, dict] = {}
    for row in data.get("klines", []):
        candle, close_ms = resolver._parse_row(row)  # noqa: SLF001 (shared row checks)
        if close_ms != candle["open_ms"] + step - 1:
            raise KronosAuthorError(f"candle {resolver.iso_ms(candle['open_ms'])} is not a {data['interval']} candle")
        rows[candle["open_ms"]] = candle
    return [rows[t] for t in sorted(rows)]


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def replay(data: dict, sampler_for: Callable[[list[dict], list[int]], Callable[[int], list[Path_]]], *,
           lookback: int, horizon: int, paths: int, batch: int, windows: int,
           stop_pct: float | None = None, target_pct: float | None = None) -> dict:
    """Replay the model at past cut points that do not overlap and score its fan.

    At each cut point the model sees only the ``lookback`` candles before it
    and predicts ``horizon`` candles. Coverage: how often the real close at
    the horizon fell inside the model's p10-p90 (80% if calibrated). With a
    stop and a target (fractions of the last close, long side), also the
    share of paths that reach the target first against what really
    happened, scored with Brier.
    """
    if lookback < 64 or horizon < 1 or windows < 1:
        raise KronosAuthorError("lookback >= 64, horizon >= 1 and windows >= 1")
    if (stop_pct is None) != (target_pct is None):
        raise KronosAuthorError("give both --stop-pct and --target-pct, or neither")
    for name, value in (("stop_pct", stop_pct), ("target_pct", target_pct)):
        if value is not None and not 0 < value < 0.5:
            raise KronosAuthorError(f"{name} must be a fraction between 0 and 0.5")
    candles = closed_candles(data)
    step = resolver.INTERVAL_MS[data["interval"]]
    cuts = []
    i = len(candles) - horizon
    while i >= lookback and len(cuts) < windows:
        block = candles[i - lookback:i + horizon]
        if block[-1]["open_ms"] - block[0]["open_ms"] == (lookback + horizon - 1) * step:
            cuts.append(i)                       # contiguous: no gaps inside
        i -= horizon                             # windows never overlap
    if not cuts:
        raise KronosAuthorError("not enough contiguous candles for one window")
    rows = []
    for i in sorted(cuts):
        history, real = candles[i - lookback:i], candles[i:i + horizon]
        future = [c["open_ms"] for c in real]
        drawn = draw(future, sampler_for(history, future), paths, batch)
        q = final_close_quantiles(drawn, ("0.1", "0.5", "0.9"))
        actual = real[-1]["close"]
        row = {"cut": resolver.iso_ms(future[0]), "p10": q["0.1"], "p50": q["0.5"], "p90": q["0.9"],
               "actual_close": actual, "inside": q["0.1"] <= actual <= q["0.9"]}
        if stop_pct is not None:
            ref = history[-1]["close"]
            rule = {"kind": "touch_first", "lower": ref * (1 - stop_pct), "upper": ref * (1 + target_pct),
                    "true_if": "upper_first", "start": resolver.iso_ms(future[0])}
            model = count(rule, future, drawn) if any(
                judge_path(rule, future, p, future[0]) != "void" for p in drawn) else None
            real_path = [(c["open"], c["high"], c["low"], c["close"]) for c in real]
            outcome = judge_path(rule, future, real_path, future[0])
            row.update({"target_first_p": model["probability"] if model else None, "real": outcome})
        rows.append(row)
    hits = sum(r["inside"] for r in rows)
    report = {"windows": len(rows), "inside_p10_p90": hits,
              "coverage": round(hits / len(rows), 4), "coverage_ci95": wilson(hits, len(rows)),
              "expected_if_calibrated": 0.8, "rows": rows}
    scored = [r for r in rows if stop_pct is not None and r["target_first_p"] is not None
              and r["real"] in ("true", "false")]
    if stop_pct is not None:
        report["stop_target"] = {
            "stop_pct": stop_pct, "target_pct": target_pct, "scored": len(scored),
            "void_or_neither": len(rows) - len(scored),
            "brier": round(sum((r["target_first_p"] - (r["real"] == "true")) ** 2 for r in scored) / len(scored), 4)
            if scored else None,
            "brier_coin": 0.25}
    if len(rows) < 30:
        report["warning"] = (f"solo {len(rows)} ventanas: el intervalo es demasiado ancho para "
                             "concluir nada; usa 30 o más")
    return report
