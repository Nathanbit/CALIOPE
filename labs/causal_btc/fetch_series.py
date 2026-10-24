#!/usr/bin/env python3
"""Download daily series for the causal lab. Standard library only.

Runs on the person's computer (the agent environment cannot reach these
sites). Public data only: no API keys, no accounts.

    python labs/causal_btc/fetch_series.py --start 2020-01-01

Series (a column fails closed and is left out, with the reason printed, if
its source does not answer):

| Column | Source |
|---|---|
| btc, eth | Binance spot daily klines (close, candle closing 00:00 UTC) |
| btc_funding | Binance USDT-M perpetual funding rate, daily mean |
| ndx, spx, gold, us10y | Stooq daily CSV (^ndx, ^spx, xauusd, 10usy.b) |

Timing: a stock close for day D (about 20:00-21:00 UTC) is known before the
BTC candle of day D+1 opens, so "X at t-1 -> btc at t" uses only past data.
Same-day links are never used as predictions.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MAX_BYTES = 10_000_000
STOOQ = {"ndx": "^ndx", "spx": "^spx", "gold": "xauusd", "us10y": "10usy.b"}


def get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "caliope-lab/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 (fixed hosts)
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("response too large")
    return raw


def day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def binance_closes(symbol: str, start_ms: int, end_ms: int) -> dict[str, float]:
    out, cursor = {}, start_ms
    while cursor < end_ms:
        query = urllib.parse.urlencode({"symbol": symbol, "interval": "1d", "startTime": cursor,
                                        "endTime": end_ms - 1, "limit": 1000})
        rows = json.loads(get(f"https://api.binance.com/api/v3/klines?{query}"))
        if not isinstance(rows, list):
            raise ValueError(f"unexpected answer: {str(rows)[:200]}")
        if not rows:
            break
        for row in rows:
            if int(row[6]) < end_ms:  # only closed daily candles
                out[day(int(row[0]))] = float(row[4])
        nxt = int(rows[-1][0]) + 86_400_000
        if nxt <= cursor:
            raise ValueError("pagination did not advance")
        cursor = nxt
    return out


def binance_funding(symbol: str, start_ms: int, end_ms: int) -> dict[str, float]:
    sums: dict[str, list[float]] = {}
    cursor = start_ms
    while cursor < end_ms:
        query = urllib.parse.urlencode({"symbol": symbol, "startTime": cursor,
                                        "endTime": end_ms - 1, "limit": 1000})
        rows = json.loads(get(f"https://fapi.binance.com/fapi/v1/fundingRate?{query}"))
        if not isinstance(rows, list):
            raise ValueError(f"unexpected answer: {str(rows)[:200]}")
        if not rows:
            break
        for row in rows:
            sums.setdefault(day(int(row["fundingTime"])), []).append(float(row["fundingRate"]))
        nxt = int(rows[-1]["fundingTime"]) + 1
        if nxt <= cursor:
            raise ValueError("pagination did not advance")
        cursor = nxt
    return {d: sum(v) / len(v) for d, v in sums.items()}


def stooq_closes(symbol: str) -> dict[str, float]:
    text = get(f"https://stooq.com/q/d/l/?s={urllib.parse.quote(symbol)}&i=d").decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "Close" not in rows[0]:
        raise ValueError(f"Stooq returned no table: {text[:120]!r}")
    return {r["Date"]: float(r["Close"]) for r in rows if r.get("Close")}


def build(start: str, end: str | None = None) -> tuple[list[dict], dict]:
    start_ms = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc) if end else \
        datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end_ms = int(end_dt.timestamp() * 1000)
    fetchers = {"btc": lambda: binance_closes("BTCUSDT", start_ms, end_ms),
                "eth": lambda: binance_closes("ETHUSDT", start_ms, end_ms),
                "btc_funding": lambda: binance_funding("BTCUSDT", start_ms, end_ms)}
    fetchers.update({name: (lambda s=sym: stooq_closes(s)) for name, sym in STOOQ.items()})
    columns, report = {}, {}
    for name, fetch in fetchers.items():
        try:
            columns[name] = {d: v for d, v in fetch().items() if start <= d < end_dt.strftime("%Y-%m-%d")}
            report[name] = {"ok": True, "days": len(columns[name])}
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            report[name] = {"ok": False, "error": str(exc)[:200]}
    if "btc" not in columns:
        raise ValueError(f"BTC is required and could not be downloaded: {report['btc']}")
    dates = sorted(set().union(*(c.keys() for c in columns.values())))
    rows = [{"date": d, **{n: columns[n].get(d, "") for n in columns}} for d in dates]
    return rows, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", help="exclusive, YYYY-MM-DD (default: today UTC)")
    args = parser.parse_args(argv)
    try:
        rows, report = build(args.start, args.end)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    out = HERE / "data" / f"series_{stamp}.csv"
    out.parent.mkdir(exist_ok=True)
    with out.open("x", encoding="utf-8", newline="") as handle:  # never overwrite
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"ok": True, "out": out.relative_to(ROOT).as_posix(), "series": report},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
