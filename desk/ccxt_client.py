"""Exchange client for the desk built on ccxt (MIT): any exchange ccxt supports.

Same interface as ``desk.blofin.Client`` (``instrument``, ``last_price``,
``equity``, ``set_leverage``, ``place_order``), so every gate in
``desk/orders.py`` stays exactly as it is: the proposal, the approval typed
at a terminal, the sealed body and the two keys for real money.

ccxt ships methods that move money out of an account (``withdraw``,
``transfer``…). The desk never touches the ccxt object directly: it goes
through ``Allowed``, which exposes a closed list of methods and raises on
anything else. ``tests/test_ccxt_desk.py`` checks the list and that no desk
source calls a forbidden method. The API key itself should still have no
withdraw or transfer permission: that remains the first line of defence.

Credentials come from environment variables at construction time:
``CALIOPE_<EXCHANGE>_API_KEY``, ``..._API_SECRET`` and ``..._API_PASSWORD``
(BloFin keeps its own ``BLOFIN_API_KEY``/``SECRET``/``PASSPHRASE``). They are
handed to ccxt and never logged or returned.
"""
from __future__ import annotations

import os
from typing import Any, Callable

from desk.blofin import BlofinError

# Every ccxt method the desk may call. Nothing that moves funds is here.
ALLOWED_METHODS = frozenset({
    "load_markets", "set_sandbox_mode", "fetch_ticker", "fetch_balance",
    "set_leverage", "create_order", "amount_to_precision", "price_to_precision",
})
# Named so a test can prove none of them is reachable.
FORBIDDEN_METHODS = ("withdraw", "transfer", "fetch_deposit_address", "create_deposit_address",
                     "fetch_withdrawals", "cancel_withdraw",
                     "transfer_out", "fetch_transfers")
DEMO, LIVE = "demo", "live"
# Exchanges where a test with the real ccxt proves the stop-loss travels in the
# order body (tests/test_ccxt_desk.py). ccxt silently drops ``stopLoss`` on
# exchanges that do not support it, and the desk never sends an order without
# its stop, so every other exchange is refused until it gets the same test.
VERIFIED_EXCHANGES = frozenset({"blofin"})


class ExchangeRefused(BlofinError):
    """The exchange answered and refused (the desk seals it as a failed result)."""


class Allowed:
    """A view of a ccxt exchange that only exposes ``ALLOWED_METHODS``.

    A guard against mistakes in the desk's own code, not a sandbox: Python
    cannot hide an object from code running in the same process. The real
    protection is an API key with no withdraw or transfer permission.
    """

    __slots__ = ("_get",)

    def __init__(self, exchange: Any):
        def get(name: str) -> Any:
            if name == "precisionMode":
                return exchange.precisionMode
            if name not in ALLOWED_METHODS:
                raise AttributeError(f"the desk does not allow ccxt.{name}")
            method = getattr(exchange, name)
            return lambda *args, **kwargs: method(*args, **kwargs)
        object.__setattr__(self, "_get", get)

    def __getattr__(self, name: str) -> Any:
        return object.__getattribute__(self, "_get")(name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("the desk does not change ccxt settings directly")


def credentials(exchange_id: str, env: dict) -> dict:
    if exchange_id == "blofin":
        names = {"apiKey": "BLOFIN_API_KEY", "secret": "BLOFIN_API_SECRET", "password": "BLOFIN_API_PASSPHRASE"}
    else:
        prefix = f"CALIOPE_{exchange_id.upper()}_API_"
        names = {"apiKey": prefix + "KEY", "secret": prefix + "SECRET", "password": prefix + "PASSWORD"}
    found = {k: env[v] for k, v in names.items() if env.get(v)}
    missing = [names[k] for k in ("apiKey", "secret") if k not in found]
    if missing:
        raise BlofinError(f"missing environment variables: {', '.join(missing)}")
    return found


def default_factory(exchange_id: str, config: dict) -> Any:
    import ccxt  # noqa: PLC0415  (only when the policy picks the ccxt client)
    if exchange_id not in ccxt.exchanges:
        raise BlofinError(f"ccxt does not know the exchange {exchange_id}")
    return getattr(ccxt, exchange_id)(config)


def _errors() -> tuple[type, type]:
    """(refusal, any ccxt error). Only ``ExchangeError`` means the exchange said no;
    every other ccxt error (network, timeout, empty or malformed answer) means the
    order may or may not exist."""
    try:
        import ccxt  # noqa: PLC0415
    except ImportError:
        return (), ()
    return ccxt.ExchangeError, ccxt.BaseError


def redact(text: str, secrets: list[str]) -> str:
    for value in secrets:
        if value and len(value) >= 4:
            text = text.replace(value, "***")
    return text


def ccxt_request(body: dict, symbol: str) -> dict:
    """The ccxt call a sealed order body implies. Pure: the same body, the same call."""
    params: dict = {"marginMode": body["marginMode"], "clientOrderId": body["clientOrderId"],
                    "stopLoss": {"triggerPrice": float(body["slTriggerPrice"])}}
    if body["positionSide"] in ("long", "short"):
        params["hedged"] = True
    if "tpTriggerPrice" in body:
        params["takeProfit"] = {"triggerPrice": float(body["tpTriggerPrice"])}
    return {"symbol": symbol, "type": body["orderType"], "side": body["side"],
            "amount": float(body["size"]), "price": float(body["price"]) if "price" in body else None,
            "params": params}


class Client:
    def __init__(self, exchange_id: str, stage: str, env: dict | None = None,
                 factory: Callable[[str, dict], Any] = default_factory):
        if stage not in (DEMO, LIVE):
            raise BlofinError("stage must be demo or live")
        if exchange_id not in VERIFIED_EXCHANGES:
            raise BlofinError(f"{exchange_id} is not verified to carry the stop-loss through ccxt; "
                              f"verified: {sorted(VERIFIED_EXCHANGES)}")
        creds = credentials(exchange_id, os.environ if env is None else env)
        self._secrets = list(creds.values())
        raw = factory(exchange_id, {"enableRateLimit": True, **creds})
        self.exchange_id, self.stage = exchange_id, stage
        self._x = Allowed(raw)
        if stage == DEMO:
            try:
                self._x.set_sandbox_mode(True)
            except (_errors()[1] or BlofinError) as exc:   # ccxt.NotSupported when there is no sandbox
                raise BlofinError(f"{exchange_id} has no demo environment in ccxt: demo stage refused") from exc
        self._markets: dict | None = None
        self.equity_source = ""

    @property
    def is_live(self) -> bool:
        return self.stage == LIVE

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        refused, base = _errors()
        try:
            return getattr(self._x, name)(*args, **kwargs)
        except refused as exc:          # the exchange answered and said no
            raise ExchangeRefused(redact(f"{self.exchange_id} refused: {exc}", self._secrets)) from exc
        except base as exc:             # network, timeout, empty or malformed answer: unknown
            raise ConnectionError(redact(str(exc), self._secrets)) from exc

    def market(self, inst_id: str) -> dict:
        if self._markets is None:
            self._markets = self._call("load_markets")
        found = [m for m in self._markets.values()
                 if m.get("id") == inst_id and m.get("swap") and m.get("linear")]
        if len(found) != 1:
            raise BlofinError(f"{inst_id}: expected one linear perpetual market on {self.exchange_id}, "
                              f"found {len(found)}")
        market = found[0]
        if market.get("active") is False:
            raise BlofinError(f"{inst_id} is not active")
        return market

    def instrument(self, inst_id: str) -> dict:
        m = self.market(inst_id)
        try:
            lot, tick = float(m["precision"]["amount"]), float(m["precision"]["price"])
            if self._x.precisionMode != 4:      # ccxt.TICK_SIZE == 4; otherwise decimal places
                lot, tick = 10.0 ** -lot, 10.0 ** -tick
            minimum = m["limits"]["amount"]["min"]
            out = {"contractValue": float(m["contractSize"]), "minSize": float(minimum if minimum else lot),
                   "lotSize": lot, "tickSize": tick,
                   "maxLeverage": float(m["limits"]["leverage"]["max"])}
        except (KeyError, TypeError, ValueError) as exc:
            raise BlofinError(f"{inst_id}: ccxt market is missing size or leverage data") from exc
        if not all(v > 0 for v in out.values()):
            raise BlofinError(f"{inst_id}: non-positive market data from ccxt")
        return out

    def last_price(self, inst_id: str) -> float:
        ticker = self._call("fetch_ticker", self.market(inst_id)["symbol"])
        last = ticker.get("last")
        if not isinstance(last, (int, float)) or last <= 0:
            raise BlofinError(f"no last price for {inst_id}")
        return float(last)

    def equity(self, settle: str = "USDT") -> float:
        balance = self._call("fetch_balance")
        total = (balance.get("total") or {}).get(settle)
        if not isinstance(total, (int, float)) or total <= 0:
            raise BlofinError(f"no {settle} total in the {self.exchange_id} balance")
        self.equity_source = f"ccxt {self.exchange_id} {self.stage} fetch_balance total {settle}"
        return float(total)

    def set_leverage(self, inst_id: str, leverage: int, margin_mode: str) -> object:
        return self._call("set_leverage", leverage, self.market(inst_id)["symbol"], {"marginMode": margin_mode})

    def _exact(self, symbol: str, request: dict) -> None:
        """Refuse before sending if ccxt would round the size or a price: what goes
        out must be what was approved."""
        checks = [("amount", "amount_to_precision", request["amount"])]
        checks += [(k, "price_to_precision", v) for k, v in (
            ("price", request["price"]), ("stop_loss", request["params"]["stopLoss"]["triggerPrice"]),
            ("take_profit", request["params"].get("takeProfit", {}).get("triggerPrice"))) if v is not None]
        for name, method, value in checks:
            rounded = float(self._call(method, symbol, value))
            if abs(rounded - value) > 1e-12 * max(1.0, abs(value)):
                raise ExchangeRefused(f"{name} {value} is not on the exchange grid (it would go out as "
                                      f"{rounded}); nothing was sent: propose it again on the grid")

    def place_order(self, body: dict) -> dict:
        request = ccxt_request(body, self.market(body["instId"])["symbol"])
        self._exact(request["symbol"], request)
        order = self._call("create_order", request["symbol"], request["type"], request["side"],
                           request["amount"], request["price"], request["params"])
        if not isinstance(order, dict) or not order.get("id"):
            # the exchange took the request but gave no id: the order may exist
            raise ConnectionError(f"{self.exchange_id} answered without an order id")
        return {"orderId": order["id"], "clientOrderId": body["clientOrderId"],
                "status": order.get("status"), "ccxt_request": request,
                "note": "ccxt adds its own brokerId tag to BloFin orders"}
