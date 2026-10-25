#!/usr/bin/env python3
"""Mirror a Polymarket "Bitcoin above X on <date>" market as a CALIOPE question.

    python labs/polymarket/mirror.py --slug bitcoin-above-82k-on-october-28 `
      --ledger projects/macroflow/predictions/ledger.jsonl

Polymarket resolves those markets with "the Binance 1 minute candle for
BTC/USDT 12:00 in the ET timezone (noon)" having a final close *higher than*
the strike. In CALIOPE that is exactly ``close_beyond`` ``above`` the strike,
on 1m candles, in a one-candle window starting at noon ET. The market's
price for "Yes" is the crowd's probability with real money behind it, so it
enters the ledger as the author ``mercado:polymarket``. Everyone else
forecasts the same question before noon ET, and ``rival`` later says who
beat the market.

It never writes to the ledger: it prints the concept and the PowerShell
commands to seal it. Standard library only; public data, no keys.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

import concepts  # noqa: E402

GAMMA = "https://gamma-api.polymarket.com/markets"
BINANCE_TICKER = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july",
                                      "august", "september", "october", "november", "december"), 1)}
SLUG = re.compile(r"bitcoin-above-(\d+(?:\.\d+)?)(k?)-on-([a-z]+)-(\d{1,2})(?:-(\d{4}))?$")
AUTHOR = "mercado:polymarket"


class MirrorError(ValueError):
    pass


def get_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "caliope-lab/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 (fixed hosts)
        return json.loads(response.read(2_000_000))


def _nth_sunday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def noon_et_utc(day: date) -> datetime:
    """Noon in New York as UTC, with the US rule (EDT from the second Sunday of
    March to the first Sunday of November). No tz database needed, which
    Windows Python lacks without the tzdata package."""
    edt = _nth_sunday(day.year, 3, 2) <= day < _nth_sunday(day.year, 11, 1)
    return datetime(day.year, day.month, day.day, 16 if edt else 17, tzinfo=timezone.utc)


def parse_slug(slug: str, today: date) -> tuple[float, date]:
    m = SLUG.fullmatch(slug.strip().lower())
    if not m:
        raise MirrorError("expected a slug like bitcoin-above-82k-on-october-28")
    number, k, month, day, year = m.groups()
    if month not in MONTHS:
        raise MirrorError(f"unknown month {month}")
    level = float(number) * (1000 if k else 1)
    when = date(int(year) if year else today.year, MONTHS[month], int(day))
    if not year and when < today:
        when = date(when.year + 1, when.month, when.day)
    return level, when


def yes_price(market: dict) -> tuple[float, str]:
    """Midpoint of best bid and ask when both exist, else the listed Yes price."""
    bid, ask = market.get("bestBid"), market.get("bestAsk")
    if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and 0 < bid <= ask < 1:
        return round((bid + ask) / 2, 4), "midpoint of best bid and ask"
    outcomes = json.loads(market.get("outcomes") or "[]")
    prices = json.loads(market.get("outcomePrices") or "[]")
    if "Yes" not in outcomes or len(prices) != len(outcomes):
        raise MirrorError("the market has no Yes price")
    return float(prices[outcomes.index("Yes")]), "listed Yes price"


def fetch_market(slug: str) -> dict:
    data = get_json(f"{GAMMA}?{urllib.parse.urlencode({'slug': slug})}")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise MirrorError(f"Polymarket returned no single market for {slug}")
    market = data[0]
    if market.get("closed"):
        raise MirrorError("the market is already closed")
    return market


def build(slug: str, market: dict, reference: float, source: str, now: datetime) -> dict:
    level, day = parse_slug(slug, now.date())
    start = noon_et_utc(day)
    if start <= now:
        raise MirrorError("noon ET of that day has passed: the window has started")
    iso = lambda t: t.isoformat().replace("+00:00", "Z")  # noqa: E731
    concept = concepts.validate_concept({
        "asset": "BTCUSDT", "event": "close_beyond", "interval": "1m", "start": iso(start),
        "horizon_end": iso(start + timedelta(minutes=1)), "reference_price": reference,
        "reference_source": source, "level": level, "side": "above"})
    probability, how = yes_price(market)
    return {"concept": concept, "probability": probability, "price_basis": how,
            "market": {"slug": slug, "question": market.get("question"), "id": market.get("id")}}


def commands(result: dict, ledger: str, now: datetime) -> str:
    concept = json.dumps(result["concept"], ensure_ascii=True, separators=(",", ":"))
    rationale = (f"Polymarket {result['market']['slug']} ({result['price_basis']}) "
                 f"a las {now.strftime('%Y-%m-%dT%H:%MZ')}")
    seal = (f"python prediction_ledger.py --ledger '{ledger}' seal --concept \"@state/polymarket.json\" "
            f"--author {AUTHOR} --probability {result['probability']} --rationale '{rationale}'")
    return ("New-Item -ItemType Directory -Force state | Out-Null\n"
            f"@'\n{concept}\n'@ | Out-File -Encoding ascii state\\polymarket.json\n"
            f"{seal} --dry-run\n{seal}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slug", required=True)
    parser.add_argument("--ledger", default="projects/macroflow/predictions/ledger.jsonl")
    parser.add_argument("--reference-price", type=float,
                        help="observed BTCUSDT price; read from Binance's public ticker if omitted")
    parser.add_argument("--reference-source")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        market = fetch_market(args.slug)
        reference, source = args.reference_price, args.reference_source
        if reference is None:
            reference = float(get_json(BINANCE_TICKER)["price"])
            source = f"Binance api ticker/price BTCUSDT {now.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        if not source:
            raise MirrorError("--reference-source is required with --reference-price")
        result = build(args.slug, market, reference, source, now)
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **result, "author": AUTHOR, "evidence_type": "observation"},
                     ensure_ascii=False, indent=2))
    print("\n# Comandos (PowerShell, carpeta CALIOPE):\n" + commands(result, args.ledger, now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
