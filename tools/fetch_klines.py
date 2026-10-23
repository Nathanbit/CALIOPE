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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", required=True, choices=sorted(resolver.INTERVAL_MS))
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if ROOT not in out.parents:
        print("--out must stay inside the CALIOPE folder", file=sys.stderr)
        return 2
    symbol = args.symbol.upper()
    if not symbol.isalnum():
        print("--symbol must be alphanumeric, e.g. BTCUSDT", file=sys.stderr)
        return 2
    try:
        start_ms = resolver.to_ms(args.start, "--start")
        end_ms = resolver.to_ms(args.end, "--end")
    except resolver.ResolutionError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    rows = []
    for url in page_urls(symbol, args.interval, start_ms, end_ms):
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 (fixed https host)
            rows.extend(json.loads(response.read().decode("utf-8")))
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"symbol": symbol, "interval": args.interval, "start": args.start, "end": args.end,
               "source": "api.binance.com/api/v3/klines",
               "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
               "klines": rows}
    out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"ok": True, "candles": len(rows), "out": str(out.relative_to(ROOT))}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
