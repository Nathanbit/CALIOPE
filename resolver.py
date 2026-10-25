"""Deterministic resolution of machine-readable questions from price candles.

This module owns two formats: question *rules* and Binance-style *klines*
files. It never sees forecasts: a resolution depends only on the rule and the
observed data, so the resolver cannot favour any author.

A klines file is JSON produced by ``tools/fetch_klines.py``::

    {"symbol": "BTCUSDT", "interval": "1m", "source": "...", "fetched_at": "...",
     "klines": [[open_time_ms, "open", "high", "low", "close", ...], ...]}

Rules (all times ISO-8601 with timezone, aligned to the interval):

- ``touch_first``: which of ``lower``/``upper`` is touched first by candle
  lows/highs. ``true_if`` is ``lower_first`` or ``upper_first``. Neither
  touched -> void. Both inside one candle -> void (order unknowable).
- ``touch``: ``side`` ``at_or_below``/``at_or_above`` ``level`` at any time.
- ``close_beyond``: a fully closed candle closes ``below``/``above`` ``level``.

Missing, gapped, duplicated, inconsistent or still-open candles fail closed:
they are never read as "nothing happened". A candle counts only if it closed
before the file's ``fetched_at``.
Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
from statistics import NormalDist
from datetime import datetime, timezone
from pathlib import Path

import evidence_io as eio

RESOLVER_VERSION = "1.2"
MAX_CANDLES = 500_000
SYMBOL = re.compile(r"[A-Z0-9]{2,30}")
# Seconds: Binance serves 1s candles; 5s-30s are built from them (``aggregate``).
AGGREGATED_FROM_1S = {"5s": 5_000, "10s": 10_000, "15s": 15_000, "30s": 30_000}
INTERVAL_MS = {"1s": 1_000, **AGGREGATED_FROM_1S, "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
               "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
RULE_KEYS = {
    "touch_first": {"kind", "symbol", "interval", "start", "lower", "upper", "true_if"},
    "touch": {"kind", "symbol", "interval", "start", "level", "side"},
    "close_beyond": {"kind", "symbol", "interval", "start", "level", "side"},
    # A numeric question: the close of the last candle of the window.
    "close_value": {"kind", "symbol", "interval", "start"},
}
QUANTILE_LEVELS = ("0.05", "0.1", "0.25", "0.5", "0.75", "0.9", "0.95")
REQUIRED_QUANTILES = ("0.1", "0.5", "0.9")
SIDES = {"touch": ("at_or_below", "at_or_above"), "close_beyond": ("below", "above")}


class ResolutionError(Exception):
    """Rule or data rejected (fail closed)."""


def to_ms(value: str, field: str) -> int:
    moment = eio.parse_aware_time(value, field, ResolutionError)
    return int(moment.timestamp() * 1000)


def iso_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _price(value, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or value <= 0:
        raise ResolutionError(f"{field} must be a positive finite number")
    return float(value)


def validate_rule(rule: dict, horizon_end: str) -> dict:
    """Return a normalized copy or raise. Strict keys, aligned times."""
    if not isinstance(rule, dict):
        raise ResolutionError("rule must be an object")
    kind = rule.get("kind")
    if kind not in RULE_KEYS:
        raise ResolutionError(f"rule.kind must be one of {sorted(RULE_KEYS)}")
    keys = set(rule)
    if keys != RULE_KEYS[kind]:
        raise ResolutionError(f"rule keys for {kind}: missing {sorted(RULE_KEYS[kind] - keys)}, "
                              f"unexpected {sorted(keys - RULE_KEYS[kind])}")
    interval = rule["interval"]
    if interval not in INTERVAL_MS:
        raise ResolutionError(f"rule.interval must be one of {sorted(INTERVAL_MS)}")
    if not isinstance(rule["symbol"], str) or not SYMBOL.fullmatch(rule["symbol"].upper()):
        raise ResolutionError("rule.symbol must be A-Z/0-9, e.g. BTCUSDT")
    step = INTERVAL_MS[interval]
    start, end = to_ms(rule["start"], "rule.start"), to_ms(horizon_end, "horizon_end")
    if start % step or end % step:
        raise ResolutionError(f"rule.start and horizon_end must align to {interval} candles")
    if start >= end:
        raise ResolutionError("rule.start must be before horizon_end")
    if (end - start) // step > MAX_CANDLES:
        raise ResolutionError(f"window too long: more than {MAX_CANDLES} candles")
    out = dict(rule, symbol=rule["symbol"].upper(), start=iso_ms(start))
    if kind == "touch_first":
        lower, upper = _price(rule["lower"], "rule.lower"), _price(rule["upper"], "rule.upper")
        if lower >= upper:
            raise ResolutionError("rule.lower must be below rule.upper")
        if rule["true_if"] not in ("lower_first", "upper_first"):
            raise ResolutionError("rule.true_if must be lower_first or upper_first")
        out.update(lower=lower, upper=upper)
    elif kind != "close_value":
        out["level"] = _price(rule["level"], "rule.level")
        if rule["side"] not in SIDES[kind]:
            raise ResolutionError(f"rule.side must be one of {SIDES[kind]}")
    return out


def load_klines(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResolutionError("klines file is not valid UTF-8 JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("klines"), list):
        raise ResolutionError("klines file must be an object with a 'klines' list")
    return data, hashlib.sha256(raw).hexdigest()


def _parse_row(row: object) -> tuple[dict, int]:
    """One Binance kline row, with consistent positive prices, or raise."""
    if not isinstance(row, list) or len(row) < 7:
        raise ResolutionError("malformed kline row: expected a list with open and close times")
    try:
        open_ms, close_ms = int(row[0]), int(row[6])
        candle = {"open_ms": open_ms, "open": float(row[1]), "high": float(row[2]),
                  "low": float(row[3]), "close": float(row[4])}
    except (TypeError, ValueError) as exc:
        raise ResolutionError("malformed kline row") from exc
    prices = ("open", "high", "low", "close")
    if not all(math.isfinite(candle[k]) and candle[k] > 0 for k in prices) \
            or candle["low"] > min(candle["open"], candle["close"]) \
            or candle["high"] < max(candle["open"], candle["close"]):
        raise ResolutionError(f"inconsistent prices in candle {iso_ms(open_ms)}")
    return candle, close_ms


def aggregate(rows: list, step_ms: int) -> list[list]:
    """Group 1s Binance rows into ``step_ms`` candles, deterministically.

    open = first open, high = max, low = min, close = last close, volume =
    sum, open time = bucket start, close time = bucket start + step - 1. A
    bucket is built only when all of its seconds are present: a missing
    second is unknown, never "no trade" (fail closed)."""
    if step_ms % 1_000 or step_ms <= 1_000:
        raise ResolutionError("aggregation needs a multiple of 1s above 1s")
    seconds: dict[int, dict] = {}
    for row in rows:
        candle, close_ms = _parse_row(row)
        if close_ms != candle["open_ms"] + 999:
            raise ResolutionError("aggregate expects 1s candles")
        if candle["open_ms"] in seconds:
            raise ResolutionError(f"duplicate candle {iso_ms(candle['open_ms'])}")
        candle["volume"] = float(row[5])
        seconds[candle["open_ms"]] = candle
    out = []
    for start in sorted({t - t % step_ms for t in seconds}):
        group = [seconds.get(start + k) for k in range(0, step_ms, 1_000)]
        if any(c is None for c in group):
            continue  # incomplete bucket: left out, so coverage checks catch it
        out.append([start, repr(group[0]["open"]), repr(max(c["high"] for c in group)),
                    repr(min(c["low"] for c in group)), repr(group[-1]["close"]),
                    repr(sum(c["volume"] for c in group)), start + step_ms - 1])
    return out


def tiebreak(rule: dict, horizon_end: str, evidence: dict, fine: dict) -> tuple[str, dict]:
    """Order the two levels of a ``touch_first`` tie with finer candles.

    Used only when both levels were touched inside one candle of the rule's
    interval. ``fine`` must be a strictly finer interval that divides it, and
    cover that whole candle; otherwise the question stays void."""
    rule = validate_rule(rule, horizon_end)
    step = INTERVAL_MS[rule["interval"]]
    fine_interval = fine.get("interval")
    if fine_interval not in INTERVAL_MS or INTERVAL_MS[fine_interval] >= step \
            or step % INTERVAL_MS[fine_interval]:
        raise ResolutionError(f"tie-break data must be a finer interval that divides {rule['interval']}")
    start = to_ms(evidence["decisive_candle"], "decisive_candle")
    end = start + step
    window = dict(rule, interval=fine_interval, start=iso_ms(start))
    candles = _candles(fine, window, iso_ms(end))
    base = {"tiebreak_interval": fine_interval, "tiebreak_candles": len(candles)}
    for c in candles:
        low_hit, high_hit = c["low"] <= rule["lower"], c["high"] >= rule["upper"]
        if low_hit and high_hit:
            return "void", dict(base, void_reason=f"both levels touched inside one {fine_interval} "
                                                   "candle too: order unknowable",
                                tiebreak_candle=iso_ms(c["open_ms"]))
        if low_hit or high_hit:
            first = "lower_first" if low_hit else "upper_first"
            return ("true" if first == rule["true_if"] else "false"), dict(
                base, first=first, tiebreak_candle=iso_ms(c["open_ms"]))
    raise ResolutionError("the finer candles do not touch either level: data disagree")


def _candles(data: dict, rule: dict, horizon_end: str) -> list[dict]:
    if str(data.get("symbol", "")).upper() != rule["symbol"]:
        raise ResolutionError("klines symbol does not match the rule")
    if data.get("interval") != rule["interval"]:
        raise ResolutionError("klines interval does not match the rule")
    step = INTERVAL_MS[rule["interval"]]
    start, end = to_ms(rule["start"], "rule.start"), to_ms(horizon_end, "horizon_end")
    fetched = to_ms(data.get("fetched_at"), "klines fetched_at")
    if fetched < end:
        raise ResolutionError("data was fetched before horizon_end: the window was still open")
    rows: dict[int, dict] = {}
    for row in data["klines"]:
        candle, close_ms = _parse_row(row)
        open_ms = candle["open_ms"]
        if not start <= open_ms < end:
            continue
        if open_ms in rows:
            raise ResolutionError(f"duplicate candle {iso_ms(open_ms)}")
        if close_ms != open_ms + step - 1 or close_ms >= fetched:
            raise ResolutionError(f"candle {iso_ms(open_ms)} was not closed when fetched")
        rows[open_ms] = candle
    expected = list(range(start, end, step))
    missing = [t for t in expected if t not in rows]
    if missing:
        raise ResolutionError(f"data does not cover the window: {len(missing)} candle(s) missing, "
                              f"first {iso_ms(missing[0])}")
    return [rows[t] for t in expected]


VOLATILITY_VERSION = "1.0"
LOOKBACK_MS = 24 * 3_600_000
MIN_RETURNS = 30


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def volatility_rival(rule: dict, horizon_end: str, data: dict,
                     lookback_ms: int = LOOKBACK_MS) -> dict:
    """What volatility alone says, using only candles that closed before the
    window started (so no outcome can leak in).

    Model: the log price is a driftless random walk with the per-candle
    volatility of the lookback. ``touch``: reflection principle,
    P = 2 * (1 - Phi(d / (sigma * sqrt(N)))), d the log distance to the level
    and N the candles in the window. ``close_beyond`` uses the same formula,
    an upper bound because closes are sampled once per candle.
    ``touch_first``: P(lower first | one is hit) = ln(U/S) / ln(U/L), as void
    outcomes are never scored. Volatility is measured per candle, so there is
    no annualization (and no 252-day trap for a market that trades every day).
    Fails closed: with gaps or fewer than ``MIN_RETURNS`` returns it reports
    ``available: false`` instead of guessing."""
    rule = validate_rule(rule, horizon_end)
    step = INTERVAL_MS[rule["interval"]]
    start, end = to_ms(rule["start"], "rule.start"), to_ms(horizon_end, "horizon_end")
    rows: dict[int, float] = {}
    for row in data.get("klines", []):
        candle, close_ms = _parse_row(row)
        open_ms = candle["open_ms"]
        if start - lookback_ms <= open_ms < start and close_ms == open_ms + step - 1:
            if open_ms in rows:
                raise ResolutionError(f"duplicate candle {iso_ms(open_ms)}")
            rows[open_ms] = candle["close"]
    expected = list(range(start - lookback_ms, start, step))
    closes = [rows[t] for t in expected if t in rows]
    base = {"available": False, "version": VOLATILITY_VERSION, "lookback_candles": len(closes)}
    if len(closes) != len(expected):
        return dict(base, reason=f"{len(expected) - len(closes)} candle(s) missing before the "
                                 "window: fetch with --lookback-hours 24")
    returns = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    if len(returns) < MIN_RETURNS:
        return dict(base, reason=f"only {len(returns)} returns before the window")
    mean = sum(returns) / len(returns)
    sigma = math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1))
    if sigma == 0:
        return dict(base, reason="zero volatility before the window")
    spot, n = closes[-1], (end - start) // step
    scale = sigma * math.sqrt(n)
    if rule["kind"] == "close_value":
        normal = NormalDist()
        return {"available": True, "version": VOLATILITY_VERSION,
                "quantiles": {q: round(spot * math.exp(normal.inv_cdf(float(q)) * scale), 2)
                              for q in QUANTILE_LEVELS},
                "sigma_per_candle": round(sigma, 8), "lookback_candles": len(closes),
                "window_candles": n, "reference_close": spot,
                "model": "driftless log random walk, lognormal quantiles"}
    if rule["kind"] == "touch_first":
        lower, upper = rule["lower"], rule["upper"]
        if not lower < spot < upper:
            return dict(base, reason="the last close before the window is outside the levels")
        p_lower = math.log(upper / spot) / math.log(upper / lower)
        p = p_lower if rule["true_if"] == "lower_first" else 1 - p_lower
    else:
        level = rule["level"]
        down = rule["side"] in ("at_or_below", "below")
        distance = math.log(spot / level) if down else math.log(level / spot)
        p = 1.0 if distance <= 0 else 2 * (1 - _phi(distance / scale))
    p = min(max(p, 0.001), 0.999)
    return {"available": True, "version": VOLATILITY_VERSION, "p": round(p, 6),
            "sigma_per_candle": round(sigma, 8), "lookback_candles": len(closes),
            "window_candles": n, "reference_close": spot,
            "model": "driftless log random walk, reflection principle"}


def step_outcome(rule: dict, high: float, low: float, close: float) -> str | None:
    """What one candle decides for a validated rule: "true", "false", "void"
    (both touch_first levels inside it) or None (not decided yet). The single
    definition used by the resolver, the bootstrap rival and model authors."""
    kind = rule["kind"]
    if kind == "touch_first":
        low_hit, high_hit = low <= rule["lower"], high >= rule["upper"]
        if low_hit and high_hit:
            return "void"
        if low_hit or high_hit:
            first = "lower_first" if low_hit else "upper_first"
            return "true" if first == rule["true_if"] else "false"
        return None
    if kind == "touch":
        hit = low <= rule["level"] if rule["side"] == "at_or_below" else high >= rule["level"]
        return "true" if hit else None
    if kind == "close_value":
        return None  # a value question is decided by the last close, not by one candle
    beyond = close < rule["level"] if rule["side"] == "below" else close > rule["level"]
    return "true" if beyond else None


def undecided_outcome(rule: dict) -> str:
    """The outcome when no candle decided: touch_first touches neither level;
    a value question always ends here, with the last close."""
    return {"touch_first": "void", "close_value": "value"}.get(rule["kind"], "false")


def evaluate(rule: dict, horizon_end: str, data: dict) -> tuple[str, dict]:
    """Return (outcome, decisive evidence) for a validated rule."""
    rule = validate_rule(rule, horizon_end)
    candles = _candles(data, rule, horizon_end)
    kind = rule["kind"]
    evidence = {"candles": len(candles), "window_start": rule["start"], "window_end": horizon_end,
                "high": max(c["high"] for c in candles), "low": min(c["low"] for c in candles)}
    if kind == "close_value":
        last = candles[-1]
        return "value", dict(evidence, value=last["close"], decisive_candle=iso_ms(last["open_ms"]))
    for c in candles:
        outcome = step_outcome(rule, c["high"], c["low"], c["close"])
        if outcome is None:
            continue
        decisive = dict(evidence, decisive_candle=iso_ms(c["open_ms"]))
        if outcome == "void":
            return "void", dict(decisive, void_reason="both levels touched inside one candle: "
                                                      "order unknowable")
        if kind == "touch_first":
            low_hit = c["low"] <= rule["lower"]
            return outcome, dict(decisive, first="lower_first" if low_hit else "upper_first")
        if kind == "close_beyond":
            return outcome, dict(decisive, close=c["close"])
        return outcome, decisive
    if kind == "touch_first":
        return "void", dict(evidence, void_reason="neither level touched before horizon_end")
    if kind == "touch":
        return "false", evidence
    below = rule["side"] == "below"
    return "false", dict(evidence, extreme_close=(min if below else max)(c["close"] for c in candles))


BOOTSTRAP_VERSION = "1.0"
BOOTSTRAP_LOOKBACK_MS = 30 * 86_400_000
BOOTSTRAP_MIN_DAYS = 7
BOOTSTRAP_NEIGHBOR_MS = 30 * 60_000
DAY_MS = 86_400_000


def bootstrap_rival(rule: dict, horizon_end: str, data: dict,
                    lookback_ms: int = BOOTSTRAP_LOOKBACK_MS) -> dict:
    """What the asset's own recent candles say, replayed many times.

    Idea from Jakeschincariol/day-trader-skill (trader-timemachine, MIT),
    reimplemented: every candle of the last 30 days before the window becomes
    four ratios to the previous close (open, high, low, close). A replay
    starts at the last close before the window and, for each candle of the
    window, draws a past candle from the same time of day (within 30 minutes)
    and scales it to the current price. The same ``step_outcome`` that
    resolves real candles decides each replay. Unlike the volatility rival,
    it keeps the real fat tails and the time-of-day pattern. Only candles
    that closed before the window are used, the seed comes from the rule, and
    with fewer than 7 days of earlier candles it reports ``available: false``.
    A replay where both touch_first levels fall in one candle is void, as in
    the resolver, and void replays do not count."""
    rule = validate_rule(rule, horizon_end)
    step = INTERVAL_MS[rule["interval"]]
    start, end = to_ms(rule["start"], "rule.start"), to_ms(horizon_end, "horizon_end")
    closes, bars = {}, {}
    for row in data.get("klines", []):
        candle, close_ms = _parse_row(row)
        t = candle["open_ms"]
        if start - lookback_ms <= t < start and close_ms == t + step - 1:
            if t in bars:
                raise ResolutionError(f"duplicate candle {iso_ms(t)}")
            bars[t] = candle
            closes[t] = candle["close"]
    base = {"available": False, "version": BOOTSTRAP_VERSION, "pool_candles": len(bars)}
    if len(bars) < BOOTSTRAP_MIN_DAYS * DAY_MS // step or start - step not in closes:
        return dict(base, reason=f"needs {BOOTSTRAP_MIN_DAYS} days of candles before the window, "
                                 "ending right before it: fetch with --lookback-hours 720")
    slots = max(1, DAY_MS // step)
    pools: dict[int, list[tuple[float, float, float]]] = {}
    for t, c in bars.items():
        previous = closes.get(t - step)
        if previous is None:
            continue
        pools.setdefault((t % DAY_MS) // step % slots, []).append(
            (c["high"] / previous, c["low"] / previous, c["close"] / previous))
    width = BOOTSTRAP_NEIGHBOR_MS // step if step < BOOTSTRAP_NEIGHBOR_MS else 0
    n_window = (end - start) // step
    window_slots = [((start + k * step) % DAY_MS) // step % slots for k in range(n_window)]
    choices = {}
    for slot in set(window_slots):
        near = [r for d in range(-width, width + 1) for r in pools.get((slot + d) % slots, [])]
        if not near:
            return dict(base, reason=f"no earlier candles near time slot {slot}")
        choices[slot] = near
    replays = min(2000, max(200, 3_000_000 // max(1, n_window)))
    seed_text = json.dumps([rule, horizon_end], sort_keys=True)
    rng_seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(rng_seed)
    counts = {"true": 0, "false": 0, "void": 0, "value": 0}
    finals = []
    spot = closes[start - step]
    for _ in range(replays):
        price, outcome = spot, None
        for slot in window_slots:
            h, lo, c = rng.choice(choices[slot])
            outcome = step_outcome(rule, price * h, price * lo, price * c)
            if outcome is not None:
                break
            price *= c
        counts[outcome or undecided_outcome(rule)] += 1
        finals.append(price)
    if rule["kind"] == "close_value":
        finals.sort()
        pick = lambda q: finals[min(len(finals) - 1, int(float(q) * len(finals)))]  # noqa: E731
        return {"available": True, "version": BOOTSTRAP_VERSION,
                "quantiles": {q: round(pick(q), 2) for q in QUANTILE_LEVELS},
                "replays": replays, "pool_candles": len(bars),
                "pool_days": round(len(bars) * step / DAY_MS, 2), "neighbor_slots": width,
                "reference_close": spot, "seed": rng_seed,
                "model": "time-of-day bootstrap of past candles (idea: trader-timemachine)"}
    counts.pop("value")
    decided = counts["true"] + counts["false"]
    if not decided:
        return dict(base, reason="every replay was void", replays=replays)
    p = min(max(counts["true"] / decided, 0.001), 0.999)
    return {"available": True, "version": BOOTSTRAP_VERSION, "p": round(p, 6),
            "replays": replays, **counts, "pool_candles": len(bars),
            "pool_days": round(len(bars) * step / DAY_MS, 2), "neighbor_slots": width,
            "reference_close": spot, "seed": rng_seed,
            "model": "time-of-day bootstrap of past candles (idea: trader-timemachine)"}
