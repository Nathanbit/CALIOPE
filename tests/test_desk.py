"""CALIOPE desk: signing, policy caps, every gate, and the full flow against a fake exchange."""
import argparse
import base64
import copy
import hashlib
import hmac
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import prediction_ledger as pl  # noqa: E402
from desk import __main__ as cli  # noqa: E402
from desk import blofin  # noqa: E402
from desk import orders as od  # noqa: E402
from desk import policy as pol  # noqa: E402

T0 = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)
INSTRUMENT = {"contractValue": 0.001, "minSize": 0.1, "lotSize": 0.1, "tickSize": 0.1, "maxLeverage": 100.0}


class FakeExchange:
    def __init__(self, equity=1000.0, price=80000.0):
        self.equity_value, self.price, self.orders, self.leverage = equity, price, [], []

    def instrument(self, inst_id):
        return dict(INSTRUMENT)

    def last_price(self, inst_id):
        return self.price

    def equity(self):
        return self.equity_value

    def set_leverage(self, inst_id, leverage, margin_mode):
        self.leverage.append((inst_id, leverage, margin_mode))
        return {"leverage": str(leverage)}

    def place_order(self, body):
        self.orders.append(body)
        return {"orderId": "123", "code": "0", "msg": "ok"}


class SigningTest(unittest.TestCase):
    def test_signature_matches_the_official_sdk_formula(self):
        # blofin-sdk-python: base64(hexdigest(hmac_sha256(secret, path+METHOD+ts+nonce+body)))
        secret, path, ts, body = "s3cret", "/api/v1/trade/order", "1700000000000", '{"a": 1}'
        expected = base64.b64encode(hmac.new(secret.encode(), (path + "POST" + ts + ts + body).encode(),
                                             hashlib.sha256).hexdigest().encode()).decode()
        self.assertEqual(blofin.sign(secret, path, "post", ts, ts, body), expected)

    def test_private_calls_sign_the_exact_body_and_never_leak_the_secret(self):
        seen = {}

        def transport(method, url, headers, body):
            seen.update(method=method, url=url, headers=headers, body=body)
            return 200, json.dumps({"code": "0", "data": [{"code": "0", "orderId": "9"}]}).encode()
        env = {"BLOFIN_API_KEY": "k", "BLOFIN_API_SECRET": "sec", "BLOFIN_API_PASSPHRASE": "p"}
        client = blofin.Client(blofin.DEMO, transport, env, clock=lambda: 1700000000.0)
        client.place_order({"instId": "BTC-USDT", "size": "1"})
        h = seen["headers"]
        self.assertEqual(seen["url"], blofin.DEMO + "/api/v1/trade/order")
        self.assertEqual(h["ACCESS-SIGN"], blofin.sign("sec", "/api/v1/trade/order", "POST",
                                                       h["ACCESS-TIMESTAMP"], h["ACCESS-NONCE"],
                                                       seen["body"].decode()))
        self.assertNotIn("sec", seen["url"] + seen["body"].decode() + json.dumps(
            {k: v for k, v in h.items() if k != "ACCESS-SIGN"}))
        with self.assertRaisesRegex(blofin.BlofinError, "missing environment"):
            blofin.Client(blofin.DEMO, transport, {}).equity()
        refuse = lambda *a: (200, b'{"code": "102002", "msg": "bad sign"}')  # noqa: E731
        with self.assertRaisesRegex(blofin.BlofinError, "bad sign"):
            blofin.Client(blofin.DEMO, refuse, env).equity()
        with self.assertRaises(blofin.BlofinError):
            blofin.Client("https://example.com")


class PolicyTest(unittest.TestCase):
    def test_the_repo_policy_is_the_chosen_one(self):
        policy, _ = pol.load()
        self.assertEqual((policy["stage"], policy["max_order_fraction"], policy["max_leverage"],
                          policy["max_daily_loss"], policy["auto"]["enabled"]), ("demo", 0.2, 5, 0.05, False))

    def test_hard_caps_and_invariants(self):
        base, _ = pol.load()
        for change, message in (({"max_leverage": 25}, "at most 20"), ({"max_daily_loss": 0.2}, "at most 0.1"),
                                ({"require_stop_loss": False}, "must stay true"),
                                ({"auto": {"enabled": True, "min_n": 5, "must_beat": ["volatility", "bootstrap"]}}, "min_n"),
                                ({"stage": "yolo"}, "stage")):
            with self.assertRaisesRegex(pol.PolicyError, message):
                pol.validate(dict(base, **change))


class SizingTest(unittest.TestCase):
    def test_whole_lots_rounded_down(self):
        self.assertEqual(od.size_contracts(1000, 80000, 0.001, 0.1), "12.5")
        self.assertEqual(od.size_contracts(1000, 80000, 0.001, 1), "12")
        self.assertEqual(od.size_contracts(10, 80000, 0.001, 1), "0")


class DeskFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.ledger, self.pred_path = d / "orders.jsonl", d / "predictions.jsonl"
        self.policy = dict(pol.load()[0], ledger=str(self.ledger), predictions=str(self.pred_path))
        with mock.patch.object(pl, "now_utc", return_value=T0 - timedelta(hours=1)):
            self.qid = pl.ask(self.pred_path, "BTC sube", "2026-10-28T00:00:00Z", "s", "c")["question_id"]
            pl.forecast(self.pred_path, self.qid, "nathan", 0.7, "r")
        self.predictions = pl.load(self.pred_path)
        self.ex = FakeExchange()
        self.now = T0
        self.clock = mock.patch.object(od, "now_utc", side_effect=lambda: self.now)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv, ask=None):
        parser_args = cli_parse(argv)
        return cli.run(parser_args, self.policy, self.ledger, self.predictions, client=self.ex,
                       ask=ask or (lambda prompt: ""))

    def propose(self, **kw):
        args = {"author": "nathan", "question": self.qid, "side": "buy", "margin": "0.1",
                "leverage": "3", "sl": "79000"}
        args.update({k: str(v) for k, v in kw.items()})
        argv = ["propose"] + [x for k, v in args.items() for x in (f"--{k}", v)]
        return self.run_cli(*argv)

    def test_full_flow_propose_approve_submit(self):
        self.run_cli("equity")
        out = self.propose(tp=83000)
        self.assertEqual(out["approved"], False)
        oid = out["order"]
        self.now += timedelta(seconds=30)
        self.run_cli("approve", "--order", oid, ask=lambda prompt: f"APRUEBO {oid}")
        self.now += timedelta(seconds=30)
        sent = self.run_cli("submit", "--order", oid)
        self.assertTrue(sent["accepted"])
        body = self.ex.orders[0]
        self.assertEqual((body["side"], body["orderType"], body["size"], body["slTriggerPrice"], body["tpTriggerPrice"]),
                         ("buy", "market", "3.7", "79000", "83000"))  # 1000*0.1*3 / 80000 / 0.001 = 3.75 -> 3.7
        self.assertEqual(self.ex.leverage, [("BTC-USDT", 3, "isolated")])
        state = od.verify(od.load(self.ledger), self.predictions)
        self.assertEqual([r["type"] for r in state.records], ["equity", "proposal", "decision", "send", "result"])

    def test_proposal_gates(self):
        self.run_cli("equity")
        cases = [({"question": "q-none"}, "no sealed forecast"), ({"author": "claude"}, "no sealed forecast"),
                 ({"leverage": 6}, "leverage"), ({"margin": 0.25}, "margin above 20%"),
                 ({"sl": 81000}, "stop-loss must be below"), ({"side": "sell", "sl": 79000}, "above it to sell"),
                 ({"tp": 79500}, "winning side"), ({"margin": 0.2, "leverage": 5, "sl": 75000}, "would lose"),
                 ({"margin": 0.0001, "leverage": 1}, "minimum size")]
        for change, message in cases:
            with self.assertRaisesRegex(od.DeskError, message, msg=str(change)):
                self.propose(**change)
        self.now += timedelta(minutes=11)
        with self.assertRaisesRegex(od.DeskError, "too old"):
            self.propose()

    def test_kill_switch_and_daily_loss(self):
        self.run_cli("equity")
        self.ex.equity_value = 940.0  # -6% today
        out = self.run_cli("equity")
        self.assertEqual(out["daily_loss"], 0.06)
        with self.assertRaisesRegex(od.DeskError, "kill switch is on"):
            self.propose()
        with self.assertRaisesRegex(od.DeskError, "confirmation"):
            self.run_cli("unkill", "--reason", "x", ask=lambda p: "no")
        self.run_cli("unkill", "--reason", "revisado", ask=lambda p: "APAGADO OFF")
        with self.assertRaisesRegex(od.DeskError, "daily loss limit"):
            self.propose()

    def test_approval_and_sending_gates(self):
        self.run_cli("equity")
        oid = self.propose()["order"]
        with self.assertRaisesRegex(od.DeskError, "only approved"):
            self.run_cli("submit", "--order", oid)
        with self.assertRaisesRegex(od.DeskError, "did not match"):
            self.run_cli("approve", "--order", oid, ask=lambda p: "si")
        self.run_cli("approve", "--order", oid, ask=lambda p: f"APRUEBO {oid}")
        self.now += timedelta(seconds=121)
        with self.assertRaisesRegex(od.DeskError, "approval expired"):
            self.run_cli("submit", "--order", oid)
        self.assertEqual(self.ex.orders, [])

    def test_auto_needs_policy_and_a_proven_author(self):
        self.run_cli("equity")
        oid = self.propose()["order"]
        state = od.verify(od.load(self.ledger), self.predictions)
        with self.assertRaisesRegex(od.DeskError, "automatic approval"):
            od.check(state, {"type": "decision", "order_id": oid, "decision": "approve", "mode": "auto",
                             "decided_by": "desk", "recorded_at": od.iso(self.now)}, self.predictions)
        with self.assertRaisesRegex(od.DeskError, "only the policy owner"):
            od.check(state, {"type": "decision", "order_id": oid, "decision": "approve", "mode": "human",
                             "decided_by": "claude", "recorded_at": od.iso(self.now)}, self.predictions)

    def test_verify_catches_a_tampered_order_even_with_a_valid_hash(self):
        self.run_cli("equity")
        oid = self.propose()["order"]
        self.run_cli("approve", "--order", oid, ask=lambda p: f"APRUEBO {oid}")
        self.run_cli("submit", "--order", oid)
        records = od.load(self.ledger)
        send = next(r for r in records if r["type"] == "send")
        i = records.index(send)
        records[i] = copy.deepcopy(send)
        records[i]["body"]["size"] = "100"
        for j in range(i, len(records)):  # re-chain everything: only the gate can catch it now
            records[j]["prev_hash"] = records[j - 1]["hash"]
            records[j]["hash"] = od._hash(records[j])
        with self.assertRaisesRegex(od.DeskError, "exactly the approved order"):
            od.verify(records, self.predictions)

    def test_a_lost_connection_is_sealed_as_unknown(self):
        self.run_cli("equity")
        oid = self.propose()["order"]
        self.run_cli("approve", "--order", oid, ask=lambda p: f"APRUEBO {oid}")
        self.ex.place_order = mock.Mock(side_effect=ConnectionResetError("reset"))
        out = self.run_cli("submit", "--order", oid)
        self.assertFalse(out["accepted"])
        self.assertTrue(out["response"]["unknown"])
        status = self.run_cli("status")
        self.assertEqual(status["unknown_outcome"], [oid])
        with self.assertRaisesRegex(od.DeskError, "already sent"):
            self.run_cli("submit", "--order", oid)  # never re-sent blindly

    def test_live_needs_both_keys(self):
        live = dict(self.policy, stage="live")
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("CALIOPE_DESK_LIVE", None)
            with self.assertRaisesRegex(od.DeskError, "both keys"):
                cli._client(live)
            os.environ["CALIOPE_DESK_LIVE"] = "si"
            self.assertTrue(cli._client(live).is_live)
            os.environ.pop("CALIOPE_DESK_LIVE")
        self.assertFalse(cli._client(self.policy).is_live)


def cli_parse(argv):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("equity")
    sub.add_parser("status")
    p = sub.add_parser("propose")
    for name in ("author", "question", "side", "inst", "rationale"):
        p.add_argument(f"--{name}", default={"inst": "BTC-USDT", "rationale": ""}.get(name))
    for name in ("margin", "sl", "tp", "limit"):
        p.add_argument(f"--{name}", type=float)
    p.add_argument("--leverage", type=int)
    for name in ("approve", "submit"):
        sub.add_parser(name).add_argument("--order")
    for name in ("kill", "unkill"):
        sub.add_parser(name).add_argument("--reason")
    return parser.parse_args(argv)


if __name__ == "__main__":
    unittest.main()
