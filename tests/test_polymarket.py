"""Polymarket "Bitcoin above X on <date>" mirrored as a CALIOPE question (author mercado:polymarket)."""
import io
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "labs" / "polymarket"))

import mirror  # noqa: E402
import prediction_ledger as pl  # noqa: E402

NOW = datetime(2026, 10, 24, 22, 0, tzinfo=timezone.utc)
MARKET = {"id": "123", "question": "Bitcoin above 82,000 on October 28?", "closed": False,
          "outcomes": "[\"Yes\", \"No\"]", "outcomePrices": "[\"0.41\", \"0.59\"]",
          "bestBid": 0.40, "bestAsk": 0.42}


class TimeAndSlugTest(unittest.TestCase):
    def test_noon_new_york_in_utc_follows_us_daylight_time(self):
        self.assertEqual(mirror.noon_et_utc(date(2026, 10, 28)).hour, 16)   # EDT
        self.assertEqual(mirror.noon_et_utc(date(2026, 11, 2)).hour, 17)    # after Nov 1
        self.assertEqual(mirror.noon_et_utc(date(2026, 3, 8)).hour, 16)     # second Sunday of March
        self.assertEqual(mirror.noon_et_utc(date(2026, 3, 7)).hour, 17)

    def test_slugs(self):
        today = date(2026, 10, 24)
        self.assertEqual(mirror.parse_slug("bitcoin-above-82k-on-october-28", today), (82000.0, date(2026, 10, 28)))
        self.assertEqual(mirror.parse_slug("bitcoin-above-112500-on-january-3", today), (112500.0, date(2027, 1, 3)))
        self.assertEqual(mirror.parse_slug("bitcoin-above-82.5k-on-october-28-2026", today)[0], 82500.0)
        with self.assertRaises(mirror.MirrorError):
            mirror.parse_slug("ethereum-above-4k-on-october-28", today)

    def test_yes_price(self):
        self.assertEqual(mirror.yes_price(MARKET), (0.41, "midpoint of best bid and ask"))
        listed = {k: v for k, v in MARKET.items() if k not in ("bestBid", "bestAsk")}
        self.assertEqual(mirror.yes_price(listed), (0.41, "listed Yes price"))
        with self.assertRaisesRegex(mirror.MirrorError, "no Yes price"):
            mirror.yes_price({"outcomes": "[\"Up\", \"Down\"]", "outcomePrices": "[\"0.5\", \"0.5\"]"})


class MirrorTest(unittest.TestCase):
    def test_the_concept_is_the_market_rule(self):
        out = mirror.build("bitcoin-above-82k-on-october-28", MARKET, 81000, "test", NOW)
        c = out["concept"]
        self.assertEqual((c["event"], c["side"], c["interval"], c["level"]), ("close_beyond", "above", "1m", 82000.0))
        self.assertEqual((c["start"], c["horizon_end"]), ("2026-10-28T16:00:00Z", "2026-10-28T16:01:00Z"))
        self.assertEqual(out["probability"], 0.41)
        with self.assertRaisesRegex(mirror.MirrorError, "has passed"):
            mirror.build("bitcoin-above-82k-on-october-24", MARKET, 81000, "test", NOW)

    def test_end_to_end_seal_forecast_and_resolve(self):
        def fake_get(url):
            return [MARKET] if "gamma-api" in url else {"price": "81234.5"}
        with mock.patch.object(mirror, "get_json", side_effect=fake_get), \
                mock.patch.object(mirror, "datetime", wraps=datetime) as dt, \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            dt.now.return_value = NOW
            self.assertEqual(mirror.main(["--slug", "bitcoin-above-82k-on-october-28"]), 0)
        text = out.getvalue()
        result = json.loads(text[:text.index("\n# Comandos")])
        self.assertIn("--author mercado:polymarket --probability 0.41", text)
        self.assertEqual(result["concept"]["reference_price"], 81234.5)
        with tempfile.TemporaryDirectory() as tmp:
            path, data = Path(tmp) / "l.jsonl", Path(tmp) / "k.json"
            with mock.patch.object(pl, "now_utc", return_value=NOW):
                qid = pl.seal(path, result["concept"], "mercado:polymarket", 0.41, "market")["question"]["question_id"]
                pl.forecast(path, qid, "nathan", 0.6, "r")
            t0 = int(datetime(2026, 10, 28, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
            data.write_text(json.dumps({"symbol": "BTCUSDT", "interval": "1m", "fetched_at": "2026-10-28T16:05:00Z",
                                        "klines": [[t0, "81990", "82100", "81950", "82000.01", "1", t0 + 59_999]]}),
                            encoding="utf-8")
            with mock.patch.object(pl, "now_utc", return_value=datetime(2026, 10, 28, 16, 6, tzinfo=timezone.utc)):
                self.assertEqual(pl.resolve_auto(path, qid, data)["outcome"], "true")  # strictly above


if __name__ == "__main__":
    unittest.main()
