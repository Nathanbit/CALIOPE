#!/usr/bin/env python3
"""Download public Binance klines for a resolution window.

Runs on the person's computer (the agent environment cannot reach Binance).
Public market data only: no API key, no account, no orders.

    python tools/fetch_klines.py --symbol BTCUSDT --interval 1m `
      --start 2026-10-21T18:17:00Z --end 2026-10-22T00:15:00Z `
      --out projects/macroflow/predictions/data/BTCUSDT_1m_20261021.json

Then resolve with ``prediction_ledger.py resolve-auto --klines <out>``.
The resolver checks coverage and gaps itself; this script only downloads.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import resolver  # noqa: E402

BASE_URL = "https://api.binance.com/api/v3/klines"
PAGE = 1000


def page_urls(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[str]:
    """URLs that cover [start_ms, end_ms) in pages of at most 1000 candles."""
    step = resolver.INTERVAL_MS[interval]
    urls, cursor = [], start_ms
    while cursor < end_ms:
        page_end = min(cursor + PAGE * step, end_ms)
        query = urllib.parse.urlencode({"symbol": symbol, "interval": interval,
                                        "startTime": cursor, "endTime": page_end - 1,
                                        "limit": PAGE})
        urls.append(f"{BASE_URL}?{query}")
        cursor = page_end
    return urls


MAX_RESPONSE_BYTES = 5_000_000


def output_path(value: Path) -> Path:
    """Only projects/<name>/predictions/data/<file>.json, never an existing file."""
    out = (ROOT / value).resolve() if not value.is_absolute() else value.resolve()
    rel = out.relative_to(ROOT).parts if ROOT in out.parents else ()
    if len(rel) != 5 or rel[0] != "projects" or rel[2:4] != ("predictions", "data") \
            or not rel[4].endswith(".json"):
        raise ValueError("--out must be projects/<project>/predictions/data/<file>.json")
    if out.exists():
        raise ValueError(f"{out.relative_to(ROOT)} already exists: data files are never overwritten")
    return out


def download(url: str) -> list:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 (fixed https host)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise ValueError(f"download failed: {exc}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("response too large")
    try:
        rows = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("response is not JSON") from exc
    if not isinstance(rows, list):
        raise ValueError(f"unexpected response (not a list of candles): {str(rows)[:200]}")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", required=True, choices=sorted(resolver.INTERVAL_MS))
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--lookback-hours", type=float, default=24.0,
                        help="also fetch this many hours before --start, for the volatility rival")
    args = parser.parse_args(argv)
    try:
        out = output_path(args.out)
        symbol = args.symbol.upper()
        if not resolver.SYMBOL.fullmatch(symbol):
            raise ValueError("--symbol must be A-Z/0-9, e.g. BTCUSDT")
        start_ms = resolver.to_ms(args.start, "--start")
        end_ms = resolver.to_ms(args.end, "--end")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        if not start_ms < end_ms <= now_ms:
            raise ValueError("need --start < --end <= now: future candles cannot be observed")
        if not 0 <= args.lookback_hours <= 168:
            raise ValueError("--lookback-hours must be between 0 and 168")
        fetch_from = start_ms - int(args.lookback_hours * 3_600_000)
        source_interval = "1s" if args.interval in resolver.AGGREGATED_FROM_1S else args.interval
        rows = []
        for url in page_urls(symbol, source_interval, fetch_from, end_ms):
            rows.extend(download(url))
        if source_interval != args.interval:
            rows = resolver.aggregate(rows, resolver.INTERVAL_MS[args.interval])
    except (ValueError, resolver.ResolutionError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"symbol": symbol, "interval": args.interval, "start": args.start, "end": args.end,
               "lookback_hours": args.lookback_hours,
               "source": "api.binance.com/api/v3/klines" + (
                   f" (1s aggregated to {args.interval})" if source_interval != args.interval else ""),
               "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
               "klines": rows}
    with out.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, separators=(",", ":")))
    print(json.dumps({"ok": True, "candles": len(rows), "out": out.relative_to(ROOT).as_posix()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
