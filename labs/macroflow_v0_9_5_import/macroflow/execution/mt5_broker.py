"""Adaptador MetaTrader 5.

Correcciones respecto a `execute_order_via_webhook` del borrador:
  - mt5.initialize() se llama UNA vez (no por orden) y se comprueba trade_allowed.
  - symbol_select() si el símbolo no está en Market Watch (si no, tick = None y crash).
  - type_filling elegido según lo que admita el símbolo (muchos brokers rechazan
    la orden con retcode 10030 si no se especifica bien).
  - SL/TP redondeados a `digits`; comentario <= 31 caracteres.
  - Se valida result.retcode == TRADE_RETCODE_DONE en vez de devolver el objeto crudo.
  - El paquete MetaTrader5 es la API oficial y solo funciona en Windows con el
    terminal instalado; el import es perezoso para que el resto del proyecto
    funcione en cualquier sistema.

No probado contra un terminal real en este entorno: verifícalo en demo primero.
"""
from __future__ import annotations

import threading

from macroflow.execution.base import Broker, OrderResult, Side
from macroflow.risk.sizing import SymbolSpec

# Bits de SymbolInfo.filling_mode (documentación MQL5: SYMBOL_FILLING_FOK=1, IOC=2)
_FILL_FOK_BIT = 1
_FILL_IOC_BIT = 2


class MT5Broker(Broker):
    def __init__(self, login: int | None = None, password: str | None = None,
                 server: str | None = None, terminal_path: str | None = None,
                 magic: int = 2026094, deviation: int = 10):
        try:
            import MetaTrader5 as mt5  # noqa: N813
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("Instala 'MetaTrader5' (solo Windows, con el terminal MT5 instalado)") from e
        self.mt5 = mt5
        self.login, self.password, self.server, self.path = login, password, server, terminal_path
        self.magic, self.deviation = magic, deviation
        self._lock = threading.Lock()  # la API de MT5 no es thread-safe
        self._connected = False

    # ---------- conexión ----------
    def connect(self) -> None:
        mt5 = self.mt5
        kwargs = {k: v for k, v in dict(path=self.path, login=self.login,
                                        password=self.password, server=self.server).items() if v}
        if not mt5.initialize(**kwargs):
            raise RuntimeError(f"mt5.initialize falló: {mt5.last_error()}")
        info = mt5.terminal_info()
        if info is None or not info.trade_allowed:
            raise RuntimeError("AutoTrading desactivado en el terminal MT5 (botón 'Algo Trading')")
        self._connected = True

    def _ensure(self) -> None:
        if not self._connected or self.mt5.terminal_info() is None:
            self._connected = False
            self.connect()

    def shutdown(self) -> None:
        self.mt5.shutdown()
        self._connected = False

    # ---------- datos ----------
    def equity(self) -> float:
        with self._lock:
            self._ensure()
            acc = self.mt5.account_info()
            if acc is None:
                raise RuntimeError(f"account_info: {self.mt5.last_error()}")
            return float(acc.equity)

    def _symbol_info(self, symbol: str):
        info = self.mt5.symbol_info(symbol)
        if info is None:
            raise KeyError(f"Símbolo no existe en el broker: {symbol}")
        if not info.visible and not self.mt5.symbol_select(symbol, True):
            raise RuntimeError(f"No se pudo activar {symbol} en Market Watch")
        return info

    def symbol_spec(self, symbol: str) -> SymbolSpec:
        with self._lock:
            self._ensure()
            i = self._symbol_info(symbol)
            vppl = i.trade_tick_value / i.trade_tick_size if i.trade_tick_size else 0.0
            if vppl <= 0:
                raise RuntimeError(f"tick_value/tick_size inválidos para {symbol}")
            return SymbolSpec(symbol, i.volume_min, i.volume_max, i.volume_step, vppl, i.digits)

    def _filling(self, info) -> int:
        mt5 = self.mt5
        if info.filling_mode & _FILL_FOK_BIT:
            return mt5.ORDER_FILLING_FOK
        if info.filling_mode & _FILL_IOC_BIT:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    # ---------- órdenes ----------
    def _send(self, request: dict, symbol: str, side: str, volume: float) -> OrderResult:
        mt5 = self.mt5
        check = mt5.order_check(request)
        if check is None or check.retcode not in (0, mt5.TRADE_RETCODE_DONE):
            msg = getattr(check, "comment", None) or str(mt5.last_error())
            return OrderResult(False, symbol, side, volume, retcode=getattr(check, "retcode", None),
                               message=f"order_check: {msg}")
        res = mt5.order_send(request)
        if res is None:
            return OrderResult(False, symbol, side, volume, message=f"order_send None: {mt5.last_error()}")
        ok = res.retcode == mt5.TRADE_RETCODE_DONE
        return OrderResult(ok, symbol, side, res.volume or volume, res.price or None, res.order or None,
                           res.retcode, res.comment)

    def market_order(self, symbol: str, side: Side, volume: float, sl: float | None,
                     tp: float | None, comment: str) -> OrderResult:
        mt5 = self.mt5
        with self._lock:
            self._ensure()
            info = self._symbol_info(symbol)
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                return OrderResult(False, symbol, side, volume, message="sin tick (mercado cerrado?)")
            price = tick.ask if side == "BUY" else tick.bid
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": float(volume),
                "type": mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL,
                "price": price,
                "deviation": self.deviation,
                "magic": self.magic,
                "comment": comment[:31],
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._filling(info),
            }
            if sl:
                req["sl"] = round(float(sl), info.digits)
            if tp:
                req["tp"] = round(float(tp), info.digits)
            return self._send(req, symbol, side, volume)

    def close_all(self, symbol: str) -> list[OrderResult]:
        mt5 = self.mt5
        out = []
        with self._lock:
            self._ensure()
            info = self._symbol_info(symbol)
            for p in mt5.positions_get(symbol=symbol) or []:
                if p.magic != self.magic:
                    continue  # no tocar posiciones manuales u otros EAs
                tick = mt5.symbol_info_tick(symbol)
                is_buy = p.type == mt5.POSITION_TYPE_BUY
                req = {
                    "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": p.volume,
                    "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                    "position": p.ticket, "price": tick.bid if is_buy else tick.ask,
                    "deviation": self.deviation, "magic": self.magic, "comment": "MacroFlow close",
                    "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(info),
                }
                out.append(self._send(req, symbol, "SELL" if is_buy else "BUY", p.volume))
        return out
