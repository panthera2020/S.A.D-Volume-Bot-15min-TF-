import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class BotDatabase:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload TEXT
                );
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty REAL NOT NULL,
                    entry_price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    order_id TEXT,
                    status TEXT NOT NULL,
                    expected_risk REAL NOT NULL,
                    notional REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS position_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT,
                    size REAL NOT NULL,
                    avg_price REAL,
                    mark_price REAL,
                    unrealised_pnl REAL NOT NULL
                );
                """
            )

    @staticmethod
    def _ts() -> str:
        return datetime.now(timezone.utc).isoformat()

    def log(self, level: str, message: str, payload: dict | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO logs (ts, level, message, payload) VALUES (?, ?, ?, ?)",
                (self._ts(), level, message, json.dumps(payload or {})),
            )

    def add_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        order_id: str,
        status: str,
        expected_risk: float,
        notional: float,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO orders
                (ts, symbol, side, qty, entry_price, stop_loss, take_profit, order_id, status, expected_risk, notional)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._ts(),
                    symbol,
                    side,
                    qty,
                    entry_price,
                    stop_loss,
                    take_profit,
                    order_id,
                    status,
                    expected_risk,
                    notional,
                ),
            )

    def add_position_snapshot(
        self,
        symbol: str,
        side: str,
        size: float,
        avg_price: float,
        mark_price: float,
        unrealised_pnl: float,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO position_snapshots
                (ts, symbol, side, size, avg_price, mark_price, unrealised_pnl)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (self._ts(), symbol, side, size, avg_price, mark_price, unrealised_pnl),
            )

    def recent_orders(self, limit: int = 100) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def recent_logs(self, limit: int = 200) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def clear_logs(self) -> int:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM logs")
            return int(cur.rowcount)

    def latest_positions(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT p1.* FROM position_snapshots p1
                INNER JOIN (
                  SELECT symbol, MAX(id) AS max_id
                  FROM position_snapshots
                  GROUP BY symbol
                ) p2
                ON p1.id = p2.max_id
                ORDER BY p1.symbol
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self._conn() as conn:
            total_orders = conn.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"]
            total_notional = conn.execute("SELECT COALESCE(SUM(notional), 0) n FROM orders").fetchone()["n"]
            total_expected_risk = conn.execute("SELECT COALESCE(SUM(expected_risk), 0) r FROM orders").fetchone()["r"]
            open_positions = conn.execute(
                """
                SELECT COUNT(*) c FROM (
                    SELECT symbol, MAX(id) AS max_id FROM position_snapshots GROUP BY symbol
                ) x
                JOIN position_snapshots p ON p.id = x.max_id
                WHERE ABS(p.size) > 0
                """
            ).fetchone()["c"]
            unrealised = conn.execute(
                """
                SELECT COALESCE(SUM(unrealised_pnl), 0) u FROM (
                    SELECT p1.* FROM position_snapshots p1
                    INNER JOIN (
                      SELECT symbol, MAX(id) AS max_id
                      FROM position_snapshots
                      GROUP BY symbol
                    ) p2
                    ON p1.id = p2.max_id
                )
                """
            ).fetchone()["u"]
        return {
            "total_orders": total_orders,
            "total_notional_usd": float(total_notional),
            "total_expected_risk_usd": float(total_expected_risk),
            "open_positions": open_positions,
            "current_unrealised_pnl": float(unrealised),
        }
