import uvicorn

from bot_engine import BotEngine
from config import BotConfig
from database import BotDatabase
from webapp import build_web_app


def main() -> None:
    cfg = BotConfig()
    cfg.validate()
    db = BotDatabase(cfg.database_path)
    engine = BotEngine(cfg, db)

    app = build_web_app(db, engine)
    uvicorn.run(app, host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()
