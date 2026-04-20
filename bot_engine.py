import threading
import time
from datetime import datetime, timezone

from bybit_client import BybitClient
from config import BotConfig
from database import BotDatabase
from strategy_logic import build_signal, enrich_indicators


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

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self.run_loop, daemon=True)
        self._thread.start()
        self.db.log("INFO", "Bot engine started")

    def stop(self) -> None:
        self.running = False
        self.db.log("INFO", "Bot engine stopped")

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
                    except Exception as exc:
                        self.db.log("ERROR", "Symbol processing failed", {"symbol": symbol, "error": str(exc)})
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
            self.db.log("INFO", f"Trade cap reached for day ({self.cfg.max_trades_per_session})")
            return

        df = enrich_indicators(df, self.cfg)
        price = float(df.iloc[-1]["close"])
        raw_qty = self.cfg.fixed_notional_usd / price
        qty = self.client.normalize_qty(symbol, raw_qty)
        if qty <= 0:
            self.db.log("WARN", "Normalized qty is zero", {"symbol": symbol, "raw_qty": raw_qty})
            return

        signal = build_signal(symbol, df, self.cfg, qty)
        if signal is None:
            self.db.log("DEBUG", "No signal", {"symbol": symbol})
            return

        if self.client.has_open_position(symbol):
            self.db.log("INFO", "Skipped signal due to existing open position", {"symbol": symbol})
            return

        # Enforce hard risk cap from user request.
        if signal.expected_risk_usd > self.cfg.risk_usd_per_trade:
            self.db.log(
                "INFO",
                "Skipped signal due to risk > $5 at fixed $3000 notional",
                {
                    "symbol": symbol,
                    "expected_risk_usd": signal.expected_risk_usd,
                    "max_risk_usd": self.cfg.risk_usd_per_trade,
                },
            )
            return

        order_id = self.client.place_entry_with_tpsl(
            symbol=symbol,
            side=signal.side,
            qty=signal.qty,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
        )
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
                "expected_risk_usd": signal.expected_risk_usd,
                "order_id": order_id,
            },
        )

    def run_test_trade(self) -> None:
        symbol = self.cfg.symbols[0]
        try:
            if self.client.has_open_position(symbol):
                self.db.log("INFO", "Test trade skipped due to existing open position", {"symbol": symbol})
                return

            df = self.client.candles(symbol, limit=2)
            price = float(df.iloc[-1]["close"])
            qty = self.client.normalize_qty(symbol, self.cfg.fixed_notional_usd / price)
            if qty <= 0:
                self.db.log("WARN", "Test trade aborted: normalized qty is zero", {"symbol": symbol})
                return

            open_side = "Buy"
            close_side = "Sell"
            open_order_id = self.client.place_market_order(symbol=symbol, side=open_side, qty=qty)
            self.db.add_order(
                symbol=symbol,
                side=open_side,
                qty=qty,
                entry_price=price,
                stop_loss=0.0,
                take_profit=0.0,
                order_id=open_order_id,
                status="TEST_OPEN",
                expected_risk=0.0,
                notional=self.cfg.fixed_notional_usd,
            )
            self.db.log("INFO", "Test trade opened", {"symbol": symbol, "qty": qty, "order_id": open_order_id})

            time.sleep(1.0)

            close_order_id = self.client.place_market_order(
                symbol=symbol, side=close_side, qty=qty, reduce_only=True
            )
            self.db.add_order(
                symbol=symbol,
                side=close_side,
                qty=qty,
                entry_price=price,
                stop_loss=0.0,
                take_profit=0.0,
                order_id=close_order_id,
                status="TEST_CLOSE",
                expected_risk=0.0,
                notional=self.cfg.fixed_notional_usd,
            )
            self.db.log("INFO", "Test trade closed after 1s", {"symbol": symbol, "qty": qty, "order_id": close_order_id})
        except Exception as exc:
            self.db.log("ERROR", "Test trade failed", {"symbol": symbol, "error": str(exc)})
