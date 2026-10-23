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
import re
from datetime import datetime, timezone
from pathlib import Path

import evidence_io as eio

RESOLVER_VERSION = "1.1"
MAX_CANDLES = 500_000
SYMBOL = re.compile(r"[A-Z0-9]{2,30}")
INTERVAL_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
               "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
RULE_KEYS = {
    "touch_first": {"kind", "symbol", "interval", "start", "lower", "upper", "true_if"},
    "touch": {"kind", "symbol", "interval", "start", "level", "side"},
    "close_beyond": {"kind", "symbol", "interval", "start", "level", "side"},
}
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
    else:
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


def evaluate(rule: dict, horizon_end: str, data: dict) -> tuple[str, dict]:
    """Return (outcome, decisive evidence) for a validated rule."""
    rule = validate_rule(rule, horizon_end)
    candles = _candles(data, rule, horizon_end)
    kind = rule["kind"]
    evidence = {"candles": len(candles), "window_start": rule["start"], "window_end": horizon_end,
                "high": max(c["high"] for c in candles), "low": min(c["low"] for c in candles)}
    if kind == "touch_first":
        for c in candles:
            low_hit, high_hit = c["low"] <= rule["lower"], c["high"] >= rule["upper"]
            if low_hit and high_hit:
                return "void", dict(evidence, decisive_candle=iso_ms(c["open_ms"]),
                                    void_reason="both levels touched inside one candle: order unknowable")
            if low_hit or high_hit:
                first = "lower_first" if low_hit else "upper_first"
                return ("true" if first == rule["true_if"] else "false"), dict(
                    evidence, first=first, decisive_candle=iso_ms(c["open_ms"]))
        return "void", dict(evidence, void_reason="neither level touched before horizon_end")
    if kind == "touch":
        below = rule["side"] == "at_or_below"
        for c in candles:
            if (c["low"] <= rule["level"]) if below else (c["high"] >= rule["level"]):
                return "true", dict(evidence, decisive_candle=iso_ms(c["open_ms"]))
        return "false", evidence
    below = rule["side"] == "below"
    for c in candles:
        if (c["close"] < rule["level"]) if below else (c["close"] > rule["level"]):
            return "true", dict(evidence, decisive_candle=iso_ms(c["open_ms"]), close=c["close"])
    return "false", dict(evidence, extreme_close=(min if below else max)(c["close"] for c in candles))
