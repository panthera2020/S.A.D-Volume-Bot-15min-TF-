import os
import threading
import time

import requests
import uvicorn

from bot_engine import BotEngine
from config import BotConfig
from database import BotDatabase
from webapp import build_web_app


def _keep_alive(url: str) -> None:
    """Ping /health every 10 min to prevent free tier spin-down."""
    while True:
        time.sleep(600)
        try:
            requests.get(f"{url}/health", timeout=5)
        except Exception:
            pass


def main() -> None:
    cfg = BotConfig()
    cfg.validate()
    db = BotDatabase(cfg.database_path)
    engine = BotEngine(cfg, db)
    app = build_web_app(db, engine)

    # Keep-alive works for both Render and Railway
    external_url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
    if external_url:
        # Railway gives domain without https://, Render gives full URL
        if not external_url.startswith("http"):
            external_url = f"https://{external_url}"
        threading.Thread(
            target=_keep_alive, args=(external_url,), daemon=True
        ).start()

    # 0.0.0.0 works on Railway, Render, and local PC
    # PORT env var is set by Railway/Render automatically; falls back to cfg.port locally
    port = int(os.environ.get("PORT", cfg.port))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()