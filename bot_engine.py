import os
import threading
import time
from datetime import datetime, timezone

import requests

from bybit_client import BybitClient
from config import BotConfig
from database import BotDatabase
from strategy_logic import build_signal, enrich_indicators

# ── Telegram helpers ──────────────────────────────────────────────────────────
_TG_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
_TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def telegram_notify(msg: str) -> None:
    if not _TG_TOKEN or not _TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage",
            json={"chat_id": _TG_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception:
        pass  # never let Telegram failure crash the bot


# ── BotEngine ─────────────────────────────────────────────────────────────────
class BotEngine:
    def __init__(self, cfg: BotConfig, db: BotDatabase):
        self.cfg = cfg
        self.db = db
        self.client = BybitClient(cfg)
        self.running = False
        self.trade_count = 0
        self.session_day = datetime.now(timezone.utc).date()
        self.last_bar_time: dict[str, int] = {}
        self._thread: threading.Thread | None = None
        self._consecutive_errors: dict[str, int] = {}   # circuit breaker

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self.run_loop, daemon=True)
        self._thread.start()
        self.db.log("INFO", "Bot engine started")
        symbols_str = " | ".join(self.cfg.symbols)
        telegram_notify(
            f"✅ <b>Bot Online</b>\n"
            f"Monitoring: {symbols_str}\n"
            f"Mode: Demo 15m | Max trades/day: {self.cfg.max_trades_per_session}"
        )

    def stop(self) -> None:
        self.running = False
        self.db.log("INFO", "Bot engine stopped")
        telegram_notify("🛑 <b>Bot stopped</b>")

    def is_running(self) -> bool:
        return self.running

    def connectivity_status(self) -> dict:
        return self.client.connectivity_check()

    def start_with_test_trade(self) -> None:
        self.start()
        thread = threading.Thread(target=self.run_test_trade, daemon=True)
        thread.start()

    def _reset_daily_counter(self) -> None:
        now_day = datetime.now(timezone.utc).date()
        if now_day != self.session_day:
            self.session_day = now_day
            self.trade_count = 0
            self.db.log("INFO", "Daily trade counter reset")

    def run_loop(self) -> None:
        while self.running:
            try:
                self._reset_daily_counter()
                for symbol in self.cfg.symbols:
                    try:
                        self.process_symbol(symbol)
                        self._consecutive_errors[symbol] = 0  # reset on success
                    except Exception as exc:
                        self._consecutive_errors[symbol] = (
                            self._consecutive_errors.get(symbol, 0) + 1
                        )
                        self.db.log(
                            "ERROR",
                            "Symbol processing failed",
                            {"symbol": symbol, "error": str(exc)},
                        )
                        # Circuit breaker — pause symbol after 5 straight errors
                        if self._consecutive_errors[symbol] >= 5:
                            self.db.log(
                                "WARN",
                                f"Too many errors for {symbol}, pausing 5 minutes",
                            )
                            telegram_notify(
                                f"⚠️ <b>{symbol}</b> — 5 consecutive errors.\n"
                                f"Pausing 5 minutes. Check your connection."
                            )
                            time.sleep(300)
                            self._consecutive_errors[symbol] = 0
            except Exception as exc:
                self.db.log("ERROR", "Loop error", {"error": str(exc)})
            time.sleep(self.cfg.loop_seconds)

    def process_symbol(self, symbol: str) -> None:
        snap = self.client.position_snapshot(symbol)
        self.db.add_position_snapshot(
            symbol=snap["symbol"],
            side=snap["side"],
            size=snap["size"],
            avg_price=snap["avg_price"],
            mark_price=snap["mark_price"],
            unrealised_pnl=snap["unrealised_pnl"],
        )

        df = self.client.candles(symbol)
        bar_time = int(df.iloc[-1]["start_time"])
        if self.last_bar_time.get(symbol) == bar_time:
            return
        self.last_bar_time[symbol] = bar_time

        if self.trade_count >= self.cfg.max_trades_per_session:
            self.db.log(
                "INFO",
                f"Trade cap reached for day ({self.cfg.max_trades_per_session})",
            )
            return

        df = enrich_indicators(df, self.cfg)
        price = float(df.iloc[-1]["close"])
        raw_qty = self.cfg.fixed_notional_usd / price
        qty = self.client.normalize_qty(symbol, raw_qty)

        if qty <= 0:
            self.db.log(
                "WARN",
                "Normalized qty is zero",
                {"symbol": symbol, "raw_qty": raw_qty},
            )
            return

        signal = build_signal(symbol, df, self.cfg, qty)
        if signal is None:
            self.db.log(
                "INFO",
                "No signal (15-min check)",
                {"symbol": symbol},
            )
            return

        if self.client.has_open_position(symbol):
            self.db.log(
                "INFO",
                "Skipped signal due to existing open position",
                {"symbol": symbol},
            )
            return

        if signal.expected_risk_usd > self.cfg.risk_usd_per_trade:
            self.db.log(
                "INFO",
                "Skipped signal: risk exceeds cap",
                {
                    "symbol": symbol,
                    "expected_risk_usd": signal.expected_risk_usd,
                    "max_risk_usd": self.cfg.risk_usd_per_trade,
                },
            )
            return

        try:
            order_id = self.client.place_entry_with_tpsl(
                symbol=symbol,
                side=signal.side,
                qty=signal.qty,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
            )
        except RuntimeError as exc:
            error_msg = str(exc)
            if "did not fill" in error_msg:
                self.db.log(
                    "INFO",
                    "Order not filled (cancelled by exchange)",
                    {"symbol": symbol},
                )
            else:
                self.db.log(
                    "ERROR",
                    "Entry aborted: TP/SL attachment failed",
                    {"symbol": symbol, "error": error_msg},
                )
            return

        self.trade_count += 1

        self.db.add_order(
            symbol=symbol,
            side=signal.side,
            qty=signal.qty,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            order_id=order_id,
            status="PLACED",
            expected_risk=signal.expected_risk_usd,
            notional=self.cfg.fixed_notional_usd,
        )
        self.db.log(
            "INFO",
            "Order placed",
            {
                "symbol": symbol,
                "side": signal.side,
                "qty": signal.qty,
                "entry_price": signal.entry_price,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "order_id": order_id,
            },
        )

        # Telegram trade alert
        direction = "📈 LONG" if signal.side == "Buy" else "📉 SHORT"
        telegram_notify(
            f"{direction} <b>{symbol}</b>\n"
            f"Entry: {signal.entry_price}\n"
            f"SL: {signal.stop_loss} | TP: {signal.take_profit}\n"
            f"Risk: ${signal.expected_risk_usd:.2f} | Reason: {signal.reason}"
        )

    def run_test_trade(self) -> None:
        symbol = self.cfg.symbols[0]
        try:
            if self.client.has_open_position(symbol):
                self.db.log(
                    "INFO",
                    "Test trade skipped: open position exists",
                    {"symbol": symbol},
                )
                return

            df = self.client.candles(symbol, limit=2)
            price = float(df.iloc[-1]["close"])
            qty = self.client.normalize_qty(
                symbol, self.cfg.fixed_notional_usd / price
            )

            if qty <= 0:
                self.db.log(
                    "WARN",
                    "Test trade aborted: normalized qty is zero",
                    {"symbol": symbol},
                )
                return

            # Open
            open_order_id = self.client.place_market_order(
                symbol=symbol, side="Buy", qty=qty
            )
            time.sleep(0.5)
            if not self.client._confirm_fill(symbol, open_order_id):
                self.db.log(
                    "ERROR",
                    "Test trade open did NOT fill on Bybit — check demo account margin or reduce fixed_notional_usd",
                    {
                        "symbol": symbol,
                        "order_id": open_order_id,
                        "qty": qty,
                        "price": price,
                    },
                )
                return

            self.db.add_order(
                symbol=symbol,
                side="Buy",
                qty=qty,
                entry_price=price,
                stop_loss=0.0,
                take_profit=0.0,
                order_id=open_order_id,
                status="TEST_OPEN",
                expected_risk=0.0,
                notional=self.cfg.fixed_notional_usd,
            )
            self.db.log(
                "INFO",
                "Test trade opened and confirmed on Bybit",
                {"symbol": symbol, "qty": qty, "order_id": open_order_id},
            )
            time.sleep(1.0)

            # Close
            close_order_id = self.client.place_market_order(
                symbol=symbol, side="Sell", qty=qty, reduce_only=True
            )
            time.sleep(0.5)
            if not self.client._confirm_fill(symbol, close_order_id):
                self.db.log(
                    "WARN",
                    "Test trade close did NOT fill — position may still be open, close manually on Bybit",
                    {"symbol": symbol, "order_id": close_order_id},
                )
                return

            self.db.add_order(
                symbol=symbol,
                side="Sell",
                qty=qty,
                entry_price=price,
                stop_loss=0.0,
                take_profit=0.0,
                order_id=close_order_id,
                status="TEST_CLOSE",
                expected_risk=0.0,
                notional=self.cfg.fixed_notional_usd,
            )
            self.db.log(
                "INFO",
                "Test trade closed and confirmed on Bybit",
                {"symbol": symbol, "qty": qty, "order_id": close_order_id},
            )

        except Exception as exc:
            self.db.log(
                "ERROR", "Test trade failed", {"symbol": symbol, "error": str(exc)}
            )