from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from bot_engine import BotEngine
from database import BotDatabase


def build_web_app(db: BotDatabase, engine: BotEngine) -> FastAPI:
    app = FastAPI(title="Bybit Donchian Volume Bot Dashboard")
    templates = Jinja2Templates(directory="templates")

    @app.get("/")
    async def dashboard(request: Request):
        return templates.TemplateResponse("index.html", {"request": request})

    @app.get("/api/summary")
    async def summary():
        return JSONResponse(
            {
                "stats": db.stats(),
                "positions": db.latest_positions(),
                "orders": db.recent_orders(100),
                "logs": db.recent_logs(200),
                "bot_running": engine.is_running(),
                "connectivity": engine.connectivity_status(),
            }
        )

    @app.post("/api/bot/start")
    async def start_bot():
        already_running = engine.is_running()
        if not already_running:
            engine.start_with_test_trade()
        return JSONResponse({"ok": True, "bot_running": engine.is_running(), "already_running": already_running})

    @app.post("/api/bot/stop")
    async def stop_bot():
        engine.stop()
        return JSONResponse({"ok": True, "bot_running": engine.is_running()})

    @app.post("/api/logs/clear")
    async def clear_logs():
        deleted = db.clear_logs()
        return JSONResponse({"ok": True, "deleted": deleted})

    return app
