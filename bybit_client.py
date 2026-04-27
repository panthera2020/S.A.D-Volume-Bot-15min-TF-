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
                testnet=False,
                demo=True,
                timeout=cfg.bybit_http_timeout,
            )
        except TypeError:
            self.session = HTTP(
                api_key=cfg.api_key,
                api_secret=cfg.api_secret,
                testnet=False,
                demo=True,
            )
        # Cache: symbol -> (qty_step, min_qty)
        self._instrument_cache: dict[str, tuple[float, float]] = {}

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
        if "errcode: 401" in msg:
            return ("auth_invalid_401", "Key/secret mismatch or wrong env.")
        if "getaddrinfo failed" in msg or "name resolution" in msg:
            return ("dns_error", "DNS resolution failed.")
        if "timeout" in msg:
            return ("network_timeout", "Network timeout.")
        return ("unknown_error", "Unknown exchange error.")

    def connectivity_check(self) -> dict:
        try:
            resp = self._request_with_retry(self.session.get_server_time)
            return {"ok": True, "message": "Connected", "server_time": resp.get("time", ""), "error_type": "", "hint": ""}
        except Exception as exc:
            message = str(exc)
            error_type, hint = self._classify_error(message)
            return {"ok": False, "message": message, "server_time": "", "error_type": error_type, "hint": hint}

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
        return pd.DataFrame(data).sort_values("start_time").reset_index(drop=True)

    def _get_instrument_info(self, symbol: str) -> tuple[float, float]:
        """Returns (qty_step, min_qty). Cached."""
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]
        resp = self._request_with_retry(
            self.session.get_instruments_info,
            category=self.cfg.category,
            symbol=symbol,
        )
        lot = resp["result"]["list"][0]["lotSizeFilter"]
        step = float(lot["qtyStep"])
        min_qty = float(lot.get("minOrderQty", step))
        self._instrument_cache[symbol] = (step, min_qty)
        return step, min_qty

    def normalize_qty(self, symbol: str, qty: float) -> float:
        step, min_qty = self._get_instrument_info(symbol)
        step_dec = Decimal(str(step))
        q = Decimal(str(qty))
        normalized = float((q / step_dec).quantize(Decimal("1"), rounding=ROUND_DOWN) * step_dec)
        if normalized < min_qty:
            return 0.0
        return normalized

    def has_open_position(self, symbol: str) -> bool:
        resp = self._request_with_retry(
            self.session.get_positions,
            category=self.cfg.category,
            symbol=symbol,
        )
        return any(abs(float(p["size"])) > 0 for p in resp["result"]["list"])

    def position_snapshot(self, symbol: str) -> dict:
        resp = self._request_with_retry(
            self.session.get_positions,
            category=self.cfg.category,
            symbol=symbol,
        )
        for p in resp["result"]["list"]:
            size = float(p["size"])
            if abs(size) > 0:
                return {
                    "symbol": symbol,
                    "side": p["side"],
                    "size": size,
                    "avg_price": float(p["avgPrice"] or 0),
                    "mark_price": float(p["markPrice"] or 0),
                    "unrealised_pnl": float(p["unrealisedPnl"] or 0),
                }
        return {"symbol": symbol, "side": "", "size": 0.0, "avg_price": 0.0, "mark_price": 0.0, "unrealised_pnl": 0.0}

    def _confirm_fill(self, symbol: str, order_id: str) -> bool:
        """Returns True if the order actually filled (fully or partially)."""
        try:
            resp = self._request_with_retry(
                self.session.get_order_history,
                category=self.cfg.category,
                symbol=symbol,
                orderId=order_id,
            )
            orders = resp["result"]["list"]
            if not orders:
                return False
            status = orders[0].get("orderStatus", "")
            return status in ("Filled", "PartiallyFilled")
        except Exception:
            return False

    def place_entry_with_tpsl(
        self,
        symbol: str,
        side: str,
        qty: float,
        stop_loss: float,
        take_profit: float,
    ) -> str:
        # Step 1: place market entry
        order = self._request_with_retry(
            self.session.place_order,
            category=self.cfg.category,
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=str(qty),
            timeInForce="IOC",
            positionIdx=0,
        )
        order_id = order["result"]["orderId"]

        # Step 2: confirm fill before attaching TP/SL
        time.sleep(0.5)
        if not self._confirm_fill(symbol, order_id):
            raise RuntimeError(
                f"Order {order_id} did not fill (IOC cancelled by exchange). "
                f"No position opened — TP/SL not set."
            )

        # Step 3: attach TP/SL — if this fails, emergency close
        try:
            self._request_with_retry(
                self.session.set_trading_stop,
                category=self.cfg.category,
                symbol=symbol,
                takeProfit=str(take_profit),
                stopLoss=str(stop_loss),
                tpslMode="Full",
                positionIdx=0,
            )
        except Exception as tpsl_exc:
            close_side = "Sell" if side == "Buy" else "Buy"
            try:
                self.place_market_order(symbol=symbol, side=close_side, qty=qty, reduce_only=True)
            except Exception:
                pass
            raise RuntimeError(
                f"set_trading_stop failed — emergency close attempted. Error: {tpsl_exc}"
            ) from tpsl_exc

        return order_id

    def place_market_order(self, symbol: str, side: str, qty: float, reduce_only: bool = False) -> str:
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