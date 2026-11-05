"""The desk on ccxt: a closed list of methods, the same gates, and a fake ccxt exchange (no network)."""
import re
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import prediction_ledger as pl  # noqa: E402
import test_desk as td  # noqa: E402
from desk import __main__ as cli  # noqa: E402
from desk import ccxt_client as cc  # noqa: E402
from desk import orders as od  # noqa: E402
from desk import policy as pol  # noqa: E402

try:
    import ccxt
    NO_SANDBOX = ccxt.NotSupported          # what ccxt raises for an exchange without a demo
except ImportError:
    NO_SANDBOX = cc.BlofinError
ENV = {"BLOFIN_API_KEY": "key-1234", "BLOFIN_API_SECRET": "secret-5678", "BLOFIN_API_PASSPHRASE": "pass-9012"}
MARKET = {"id": "BTC-USDT", "symbol": "BTC/USDT:USDT", "swap": True, "linear": True, "active": True,
          "contractSize": 0.001, "precision": {"amount": 0.1, "price": 0.1},
          "limits": {"amount": {"min": 0.1}, "leverage": {"max": 100}}}


class FakeCcxt:
    """Looks like a ccxt exchange, including the methods the desk must never reach."""
    precisionMode = 4
    id = "blofin"

    def __init__(self, config, sandbox=True):
        self.config, self.calls, self.sandbox_ok = config, [], sandbox

    def set_sandbox_mode(self, on):
        if not self.sandbox_ok:
            raise NO_SANDBOX("no sandbox")
        self.calls.append(("sandbox", on))

    def load_markets(self):
        return {"BTC/USDT:USDT": MARKET, "BTC/USDT": dict(MARKET, swap=False, symbol="BTC/USDT")}

    def fetch_ticker(self, symbol):
        return {"last": 80000.0}

    balance = 1000.0

    def fetch_balance(self):
        return {"total": {"USDT": self.balance}}

    def set_leverage(self, leverage, symbol, params):
        self.calls.append(("leverage", leverage, symbol, params))
        return {"ok": True}

    def amount_to_precision(self, symbol, amount):
        return str(int(round(amount * 10, 9)) / 10)      # lot 0.1, truncating like ccxt

    def price_to_precision(self, symbol, price):
        return str(round(price, 1))                      # tick 0.1

    def create_order(self, symbol, type_, side, amount, price, params):
        self.calls.append(("order", symbol, type_, side, amount, price, params))
        return {"id": "987", "status": "open"}

    def withdraw(self, *a, **k):  # pragma: no cover - must be unreachable
        raise AssertionError("withdraw reached")

    def transfer(self, *a, **k):  # pragma: no cover
        raise AssertionError("transfer reached")


class TestClient(cc.Client):
    """The ccxt client, plus the two views the shared desk tests read from their fake."""

    @property
    def equity_value(self):
        return self._fake.balance

    @equity_value.setter
    def equity_value(self, value):
        self._fake.balance = value

    @property
    def orders(self):
        return [c for c in self._fake.calls if c[0] == "order"]


def client(sandbox=True, env=ENV):
    holder = {}

    def factory(exchange_id, config):
        holder["x"] = FakeCcxt(config, sandbox)
        return holder["x"]
    c = TestClient("blofin", "demo", env=env, factory=factory)
    c._fake = holder["x"]
    return c, holder


class AllowListTest(unittest.TestCase):
    def test_no_method_that_moves_money_is_allowed(self):
        self.assertFalse(cc.ALLOWED_METHODS & set(cc.FORBIDDEN_METHODS))
        c, h = client()
        for name in cc.FORBIDDEN_METHODS + ("private_post_asset_transfer", "fetch_deposits"):
            with self.assertRaises(AttributeError, msg=name):
                getattr(c._x, name)
        with self.assertRaises(AttributeError):
            c._x.apiKey = "other"

    def test_no_desk_source_calls_a_forbidden_method(self):
        pattern = re.compile(r"\.(" + "|".join(cc.FORBIDDEN_METHODS) + r")\s*\(")
        for path in (ROOT / "desk").glob("*.py"):
            self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")), path.name)

    def test_demo_forces_the_sandbox_or_refuses(self):
        _, h = client()
        self.assertEqual(h["x"].calls[0], ("sandbox", True))
        with self.assertRaisesRegex(cc.BlofinError, "no demo environment"):
            client(sandbox=False)

    def test_missing_keys_fail_and_values_never_show(self):
        with self.assertRaisesRegex(cc.BlofinError, "BLOFIN_API_KEY"):
            client(env={})
        self.assertIn("CALIOPE_OKX_API_KEY", str(self._missing("okx")))
        self.assertEqual(cc.credentials("blofin", {"BLOFIN_API_KEY": "a", "BLOFIN_API_SECRET": "b",
                                                   "BLOFIN_API_PASSPHRASE": "c"})["password"], "c")


    def _missing(self, exchange_id):
        try:
            cc.credentials(exchange_id, {})
        except cc.BlofinError as exc:
            return exc

    def test_unverified_exchanges_are_refused_because_the_stop_may_be_dropped(self):
        with self.assertRaisesRegex(cc.BlofinError, "not verified to carry the stop-loss"):
            cc.Client("mexc", "demo", env={"CALIOPE_MEXC_API_KEY": "k", "CALIOPE_MEXC_API_SECRET": "s"},
                      factory=lambda i, c: FakeCcxt(c))

    def test_the_raw_exchange_is_not_an_attribute(self):
        c, _ = client()
        for name in ("_exchange", "options", "markets", "apiKey", "secret"):
            with self.assertRaises(AttributeError, msg=name):
                getattr(c._x, name)
        self.assertFalse(hasattr(c._x.fetch_ticker, "__self__"))


class ClientTest(unittest.TestCase):
    def test_instrument_and_prices_from_the_one_perpetual(self):
        c, _ = client()
        self.assertEqual(c.instrument("BTC-USDT"), td.INSTRUMENT)
        self.assertEqual(c.last_price("BTC-USDT"), 80000.0)
        self.assertEqual(c.equity(), 1000.0)
        self.assertIn("ccxt blofin demo", c.equity_source)
        with self.assertRaisesRegex(cc.BlofinError, "found 0"):
            c.instrument("ETH-USDT")

    def test_off_grid_prices_are_refused_before_anything_is_sent(self):
        c, h = client()
        body = {"instId": "BTC-USDT", "marginMode": "isolated", "positionSide": "net", "side": "buy",
                "orderType": "market", "size": "3.7", "clientOrderId": "oabc", "slTriggerPrice": "79000.123",
                "slOrderPrice": "-1"}
        with self.assertRaisesRegex(cc.ExchangeRefused, "stop_loss 79000.123 is not on the exchange grid"):
            c.place_order(body)
        with self.assertRaisesRegex(cc.ExchangeRefused, "amount"):
            c.place_order(dict(body, slTriggerPrice="79000", size="3.75"))
        self.assertFalse([x for x in h["x"].calls if x[0] == "order"])

    def test_an_answer_without_id_is_unknown_not_rejected(self):
        c, h = client()
        h["x"].create_order = lambda *a: {"status": "open"}
        body = {"instId": "BTC-USDT", "marginMode": "isolated", "positionSide": "net", "side": "buy",
                "orderType": "market", "size": "3.7", "clientOrderId": "oabc", "slTriggerPrice": "79000",
                "slOrderPrice": "-1"}
        with self.assertRaises(ConnectionError):
            c.place_order(body)

    def test_body_maps_to_one_ccxt_call(self):
        body = {"instId": "BTC-USDT", "marginMode": "isolated", "positionSide": "net", "side": "buy",
                "orderType": "limit", "size": "3.7", "clientOrderId": "oabc", "slTriggerPrice": "79000",
                "slOrderPrice": "-1", "price": "80100", "tpTriggerPrice": "83000", "tpOrderPrice": "-1"}
        req = cc.ccxt_request(body, "BTC/USDT:USDT")
        self.assertEqual(req, {"symbol": "BTC/USDT:USDT", "type": "limit", "side": "buy", "amount": 3.7,
                               "price": 80100.0, "params": {"marginMode": "isolated", "clientOrderId": "oabc",
                                                            "stopLoss": {"triggerPrice": 79000.0},
                                                            "takeProfit": {"triggerPrice": 83000.0}}})
        self.assertTrue(cc.ccxt_request(dict(body, positionSide="long"), "s")["params"]["hedged"])

    def test_real_ccxt_blofin_sends_the_same_body_as_the_native_client(self):
        import importlib.util
        if importlib.util.find_spec("ccxt") is None:
            self.skipTest("ccxt no está instalado")
        import ccxt
        x = ccxt.blofin({"apiKey": "k", "secret": "s", "password": "p"})
        m = {"id": "BTC-USDT", "symbol": "BTC/USDT:USDT", "base": "BTC", "quote": "USDT", "settle": "USDT",
             "swap": True, "linear": True, "contract": True, "contractSize": 0.001, "type": "swap",
             "spot": False, "active": True, "precision": {"amount": 0.1, "price": 0.1},
             "limits": {"amount": {"min": 0.1}}}
        x.markets, x.markets_by_id, x.symbols = {m["symbol"]: m}, {m["id"]: [m]}, [m["symbol"]]
        proposal = {"inst_id": "BTC-USDT", "side": "buy", "limit_price": None, "contracts": "3.7",
                    "order_id": "o-abc", "stop_loss": 79000.0, "take_profit": 83000.0,
                    "limits": {"margin_mode": "isolated", "position_mode": "net"}}
        native = od.order_body(proposal)
        r = cc.ccxt_request(native, m["symbol"])
        sent = x.create_order_request(r["symbol"], r["type"], r["side"], r["amount"], r["price"], r["params"])
        sent.pop("brokerId", None)                       # ccxt's own referral tag
        self.assertEqual(sent, {k: v for k, v in native.items() if k != "positionSide"})

    def test_network_errors_become_unknown_and_refusals_stay_refusals(self):
        import importlib.util
        if importlib.util.find_spec("ccxt") is None:
            self.skipTest("ccxt no está instalado")
        import ccxt
        c, h = client()
        h["x"].create_order = mock.Mock(side_effect=ccxt.RequestTimeout("t"))
        with self.assertRaises(ConnectionError):
            c._call("create_order", "s", "market", "buy", 1, None, {})
        h["x"].create_order = mock.Mock(side_effect=ccxt.InsufficientFunds("no"))
        with self.assertRaises(cc.ExchangeRefused):
            c._call("create_order", "s", "market", "buy", 1, None, {})
        for ambiguous in (ccxt.BadResponse("empty"), ccxt.NullResponse("null")):
            h["x"].create_order = mock.Mock(side_effect=ambiguous)
            with self.assertRaises(ConnectionError, msg=type(ambiguous).__name__):
                c._call("create_order", "s", "market", "buy", 1, None, {})
        h["x"].create_order = mock.Mock(side_effect=ccxt.AuthenticationError("bad key key-1234 in url"))
        with self.assertRaises(cc.ExchangeRefused) as caught:
            c._call("create_order", "s", "market", "buy", 1, None, {})
        self.assertNotIn("key-1234", str(caught.exception))
        self.assertIn("***", str(caught.exception))


class PolicyTest(unittest.TestCase):
    def test_exchange_key_is_optional_and_strict(self):
        base = pol.load()[0]
        self.assertEqual(pol.exchange_of(base), {"id": "blofin", "client": "native"})
        okx = dict(base, exchange={"id": "okx", "client": "ccxt"}, instruments=["BTC-USDT"])
        self.assertIs(pol.validate(okx), okx)
        for bad in ({"id": "okx", "client": "native"}, {"id": "OKX", "client": "ccxt"},
                    {"id": "okx"}, {"id": "../x", "client": "ccxt"}):
            with self.assertRaises(pol.PolicyError, msg=bad):
                pol.validate(dict(base, exchange=bad))
        with self.assertRaises(pol.PolicyError):
            pol.validate(dict(base, other=1))


class CcxtFlowTest(td.DeskFlowTest):
    """Every desk gate test again, now with the ccxt client in front of a fake exchange."""

    def setUp(self):
        super().setUp()
        self.ccxt, self.holder = client()
        self.policy = dict(self.policy, exchange={"id": "blofin", "client": "ccxt"})
        self.ex = self.ccxt

    def test_full_flow_propose_approve_submit(self):
        self.run_cli("equity")
        oid = self.propose(tp=83000)["order"]
        self.now += timedelta(seconds=30)
        self.run_cli("approve", "--order", oid, ask=lambda prompt: f"APRUEBO {oid}")
        self.now += timedelta(seconds=30)
        sent = self.run_cli("submit", "--order", oid)
        self.assertTrue(sent["accepted"])
        calls = self.holder["x"].calls
        self.assertEqual(calls[1], ("leverage", 3, "BTC/USDT:USDT", {"marginMode": "isolated"}))
        _, symbol, kind, side, amount, price, params = calls[2]
        self.assertEqual((symbol, kind, side, amount, price), ("BTC/USDT:USDT", "market", "buy", 3.7, None))
        self.assertEqual(params["stopLoss"], {"triggerPrice": 79000.0})
        state = od.verify(od.load(self.ledger), self.predictions)
        self.assertEqual(state.results[oid]["response"]["ccxt_request"]["params"]["clientOrderId"],
                         oid.replace("-", ""))

    def test_a_lost_connection_is_sealed_as_unknown(self):
        self.run_cli("equity")
        oid = self.propose()["order"]
        self.run_cli("approve", "--order", oid, ask=lambda p: f"APRUEBO {oid}")
        self.holder["x"].create_order = mock.Mock(side_effect=ConnectionResetError("reset"))
        out = self.run_cli("submit", "--order", oid)
        self.assertTrue(out["response"]["unknown"])

    def test_live_needs_both_keys(self):
        live = dict(self.policy, stage="live")
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("CALIOPE_DESK_LIVE", None)
            with self.assertRaisesRegex(od.DeskError, "both keys"):
                cli._client(live)


if __name__ == "__main__":
    unittest.main()
