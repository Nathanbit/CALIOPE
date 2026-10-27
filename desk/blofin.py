"""Minimal BloFin REST client for the desk. Standard library only.

Signing follows BloFin's official Python SDK (blofin/blofin-sdk-python,
``client._sign_request``) and ccxt's ``blofin.sign``, which agree:
``base64(hex(HMAC-SHA256(secret, path + METHOD + timestamp + nonce + body)))``
with headers ACCESS-KEY, ACCESS-SIGN, ACCESS-TIMESTAMP, ACCESS-NONCE and
ACCESS-PASSPHRASE. The body string that is signed is the exact string sent.

Credentials are read from environment variables at call time and are never
stored, logged or returned: BLOFIN_API_KEY, BLOFIN_API_SECRET,
BLOFIN_API_PASSPHRASE. Create the key with READ and TRADE only (no TRANSFER),
bound to your IP.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

DEMO = "https://demo-trading-openapi.blofin.com"
LIVE = "https://openapi.blofin.com"
ENV_VARS = ("BLOFIN_API_KEY", "BLOFIN_API_SECRET", "BLOFIN_API_PASSPHRASE")
Transport = Callable[[str, str, dict, bytes | None], tuple[int, bytes]]


class BlofinError(RuntimeError):
    def __init__(self, message: str, code: str | None = None, response: object = None):
        super().__init__(message)
        self.code, self.response = code, response


def sign(secret: str, request_path: str, method: str, timestamp: str, nonce: str, body: str) -> str:
    prehash = request_path + method.upper() + timestamp + nonce + body
    digest = hmac.new(secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).hexdigest()
    return base64.b64encode(digest.encode("utf-8")).decode("utf-8")


def _urllib_transport(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 (fixed hosts)
            return response.status, response.read(2_000_000)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(2_000_000)


class Client:
    def __init__(self, base_url: str, transport: Transport | None = None,
                 env: dict | None = None, clock: Callable[[], float] = time.time):
        if base_url not in (DEMO, LIVE):
            raise BlofinError("unknown BloFin base URL")
        self.base_url = base_url
        self._transport = transport or _urllib_transport
        self._env = os.environ if env is None else env
        self._clock = clock

    @property
    def is_live(self) -> bool:
        return self.base_url == LIVE

    def _call(self, method: str, path: str, params: dict | None = None,
              body: dict | None = None, private: bool = False) -> object:
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        body_text = json.dumps(body) if body is not None else ""
        headers = {"Content-Type": "application/json"}
        if private:
            missing = [v for v in ENV_VARS if not self._env.get(v)]
            if missing:
                raise BlofinError(f"missing environment variables: {', '.join(missing)}")
            timestamp = str(int(self._clock() * 1000))
            nonce = timestamp
            headers.update({
                "ACCESS-KEY": self._env["BLOFIN_API_KEY"],
                "ACCESS-SIGN": sign(self._env["BLOFIN_API_SECRET"], path + query, method,
                                    timestamp, nonce, body_text),
                "ACCESS-TIMESTAMP": timestamp, "ACCESS-NONCE": nonce,
                "ACCESS-PASSPHRASE": self._env["BLOFIN_API_PASSPHRASE"]})
        status, raw = self._transport(method, self.base_url + path + query, headers,
                                      body_text.encode("utf-8") if body is not None else None)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BlofinError(f"HTTP {status}: answer is not JSON") from None
        if status != 200 or str(data.get("code")) != "0":
            raise BlofinError(f"BloFin refused: {data.get('msg')}", str(data.get("code")), data)
        return data.get("data")

    def instrument(self, inst_id: str) -> dict:
        rows = self._call("GET", "/api/v1/market/instruments", {"instId": inst_id})
        if not isinstance(rows, list) or len(rows) != 1:
            raise BlofinError(f"instrument {inst_id} not found")
        row = rows[0]
        if row.get("state") not in (None, "live"):
            raise BlofinError(f"{inst_id} is not live")
        return {k: float(row[k]) for k in ("contractValue", "minSize", "lotSize", "tickSize", "maxLeverage")}

    def last_price(self, inst_id: str) -> float:
        rows = self._call("GET", "/api/v1/market/tickers", {"instId": inst_id})
        if not isinstance(rows, list) or not rows:
            raise BlofinError(f"no ticker for {inst_id}")
        return float(rows[0]["last"])

    def equity(self) -> float:
        data = self._call("GET", "/api/v1/account/balance", private=True)
        return float(data["totalEquity"])

    def set_leverage(self, inst_id: str, leverage: int, margin_mode: str) -> object:
        return self._call("POST", "/api/v1/account/set-leverage", body={
            "instId": inst_id, "leverage": str(leverage), "marginMode": margin_mode}, private=True)

    def place_order(self, body: dict) -> dict:
        data = self._call("POST", "/api/v1/trade/order", body=body, private=True)
        row = data[0] if isinstance(data, list) and data else data
        if not isinstance(row, dict) or str(row.get("code", "0")) != "0":
            raise BlofinError(f"order rejected: {row}", None, data)
        return row
