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


def probability(rule: dict, horizon_end: str, future: list[int], sample: Callable[[int], list[Path_]],
                paths: int, batch: int) -> dict:
    """Draw ``paths`` independent paths in batches and count outcomes."""
    rule = resolver.validate_rule(rule, horizon_end)
    start = resolver.to_ms(rule["start"], "start")
    counts = {"true": 0, "false": 0, "void": 0}
    drawn = 0
    while drawn < paths:
        size = min(batch, paths - drawn)
        got = sample(size)
        if len(got) != size:
            raise KronosAuthorError("the model returned a different number of paths")
        for path in got:
            counts[judge_path(rule, future, path, start)] += 1
        drawn += size
    decided = counts["true"] + counts["false"]
    if not decided:
        raise KronosAuthorError("every path was void: no probability")
    return {"probability": round(counts["true"] / decided, 4), "paths": paths, **counts}
