from decimal import Decimal, ROUND_DOWN
import time
from typing import Any, Callable

import pandas as pd
from pybit.unified_trading import HTTP

from config import BotConfig


class BybitClient:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        try:
            self.session = HTTP(
                api_key=cfg.api_key,
                api_secret=cfg.api_secret,
                testnet=cfg.testnet,
                demo=cfg.demo,
                timeout=cfg.bybit_http_timeout,
            )
        except TypeError:
            # Some pybit versions may not expose timeout in constructor.
            self.session = HTTP(api_key=cfg.api_key, api_secret=cfg.api_secret, testnet=cfg.testnet, demo=cfg.demo)
        self.step_cache: dict[str, float] = {}

    def _request_with_retry(self, fn: Callable[..., Any], **kwargs: Any) -> Any:
        retries = max(1, self.cfg.bybit_http_retries)
        wait_s = max(0.1, self.cfg.bybit_retry_backoff_seconds)
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                return fn(**kwargs)
            except Exception as exc:
                last_exc = exc
                if attempt >= retries:
                    raise
                time.sleep(wait_s * attempt)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Unknown Bybit request failure")

    @staticmethod
    def _classify_error(message: str) -> tuple[str, str]:
        msg = (message or "").lower()
        if "errcode: 401" in msg or "http status code is not 200. (errcode: 401)" in msg:
            return (
                "auth_invalid_401",
                "Auth invalid (401): key/secret mismatch, wrong env (mainnet/testnet/demo), or API key IP whitelist mismatch.",
            )
        if "getaddrinfo failed" in msg or "name resolution" in msg or "failed to resolve" in msg:
            return ("dns_error", "DNS resolution failed: cannot resolve Bybit host.")
        if "read timed out" in msg or "handshake operation timed out" in msg or "timeout" in msg:
            return ("network_timeout", "Network timeout reaching Bybit API.")
        return ("unknown_error", "Unknown exchange connectivity error.")

    def connectivity_check(self) -> dict:
        try:
            resp = self._request_with_retry(self.session.get_server_time)
            return {
                "ok": True,
                "message": "Connected",
                "server_time": resp.get("time", ""),
                "error_type": "",
                "hint": "",
            }
        except Exception as exc:
            message = str(exc)
            error_type, hint = self._classify_error(message)
            return {
                "ok": False,
                "message": message,
                "server_time": "",
                "error_type": error_type,
                "hint": hint,
            }

    def candles(self, symbol: str, limit: int = 300) -> pd.DataFrame:
        resp = self._request_with_retry(
            self.session.get_kline,
            category=self.cfg.category,
            symbol=symbol,
            interval=self.cfg.timeframe,
            limit=limit,
        )
        rows = resp["result"]["list"]
        data = [
            {
                "start_time": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for r in rows
        ]
        df = pd.DataFrame(data).sort_values("start_time").reset_index(drop=True)
        return df

    def _qty_step(self, symbol: str) -> float:
        if symbol in self.step_cache:
            return self.step_cache[symbol]
        resp = self._request_with_retry(
            self.session.get_instruments_info, category=self.cfg.category, symbol=symbol
        )
        step = float(resp["result"]["list"][0]["lotSizeFilter"]["qtyStep"])
        self.step_cache[symbol] = step
        return step

    def normalize_qty(self, symbol: str, qty: float) -> float:
        step = Decimal(str(self._qty_step(symbol)))
        q = Decimal(str(qty))
        normalized = (q / step).quantize(Decimal("1"), rounding=ROUND_DOWN) * step
        return float(normalized)

    def has_open_position(self, symbol: str) -> bool:
        resp = self._request_with_retry(self.session.get_positions, category=self.cfg.category, symbol=symbol)
        positions = resp["result"]["list"]
        for p in positions:
            if abs(float(p["size"])) > 0:
                return True
        return False

    def position_snapshot(self, symbol: str) -> dict:
        resp = self._request_with_retry(self.session.get_positions, category=self.cfg.category, symbol=symbol)
        positions = resp["result"]["list"]
        for p in positions:
            size = float(p["size"])
            if abs(size) > 0:
                side = p["side"]
                return {
                    "symbol": symbol,
                    "side": side,
                    "size": size,
                    "avg_price": float(p["avgPrice"] or 0),
                    "mark_price": float(p["markPrice"] or 0),
                    "unrealised_pnl": float(p["unrealisedPnl"] or 0),
                }
        return {"symbol": symbol, "side": "", "size": 0.0, "avg_price": 0.0, "mark_price": 0.0, "unrealised_pnl": 0.0}

    def place_entry_with_tpsl(
        self,
        symbol: str,
        side: str,
        qty: float,
        stop_loss: float,
        take_profit: float,
    ) -> str:
        order = self._request_with_retry(
            self.session.place_order,
            category=self.cfg.category,
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=str(qty),
            timeInForce="IOC",
        )
        order_id = order["result"]["orderId"]
        self._request_with_retry(
            self.session.set_trading_stop,
            category=self.cfg.category,
            symbol=symbol,
            takeProfit=str(take_profit),
            stopLoss=str(stop_loss),
            tpslMode="Full",
            positionIdx=0,
        )
        return order_id

    def place_market_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        reduce_only: bool = False,
    ) -> str:
        order = self._request_with_retry(
            self.session.place_order,
            category=self.cfg.category,
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=str(qty),
            timeInForce="IOC",
            reduceOnly=reduce_only,
            positionIdx=0,
        )
        return order["result"]["orderId"]
