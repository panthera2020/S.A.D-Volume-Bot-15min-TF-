import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class BotConfig:
    api_key: str = os.getenv("BYBIT_API_KEY", "")
    api_secret: str = os.getenv("BYBIT_API_SECRET", "")
    testnet: bool = os.getenv("BYBIT_TESTNET", "true").lower() == "true"
    demo: bool = os.getenv("BYBIT_DEMO", "false").lower() == "true"
    loop_seconds: int = int(os.getenv("BOT_LOOP_SECONDS", "20"))
    database_path: str = os.getenv("DATABASE_PATH", "bot_data.db")
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8000"))
    bybit_http_timeout: int = int(os.getenv("BYBIT_HTTP_TIMEOUT", "30"))
    bybit_http_retries: int = int(os.getenv("BYBIT_HTTP_RETRIES", "3"))
    bybit_retry_backoff_seconds: float = float(os.getenv("BYBIT_RETRY_BACKOFF_SECONDS", "1.5"))

    symbols: list[str] = field(default_factory=lambda: ["SOLUSDT", "AVAXUSDT", "DOGEUSDT"])
    category: str = "linear"
    timeframe: str = "15"

    donchian_len: int = 20
    rsi_len: int = 14
    adx_len: int = 14
    risk_rr: float = 1.0
    swing_lookback: int = 10
    volume_multiplier: float = 1.0
    max_trades_per_session: int = 20

    risk_usd_per_trade: float = 5.0
    fixed_notional_usd: float = 3000.0

    def validate(self) -> None:
        if not self.api_key or not self.api_secret:
            raise ValueError("Missing BYBIT_API_KEY or BYBIT_API_SECRET in .env")
        if self.testnet and self.demo:
            raise ValueError("Invalid mode: BYBIT_TESTNET and BYBIT_DEMO cannot both be true.")
