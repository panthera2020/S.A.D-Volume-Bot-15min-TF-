# Bybit Donchian Volume Bot (15m)

Local Python bot based on your Pine strategy logic, with a web dashboard.

## Locked Trading Scope

- Exchange: Bybit Perpetual (via `pybit`)
- Symbols: `SOLUSDT`, `AVAXUSDT`, `DOGEUSDT`
- Timeframe: `15m` only
- Entry logic:
  - Donchian breakout/breakdown using previous band value
  - Trend from Donchian midline
  - RSI filter (`>40` long, `<60` short)
  - Volume spike (`volume > sma(volume, 20) * 1.0`)
- Stop loss: swing low/high over 10 bars
- Take profit: 1:1 RR
- Max trades per day: 20

## Risk and Position Constraints

- Position notional fixed: `$3000` per trade.
- Risk cap fixed: `$5` max expected loss at stop.
- If signal risk at $3000 size is above $5, trade is skipped to enforce both constraints.

## Setup

1. Create and activate virtual environment:
   - Windows PowerShell:
     - `python -m venv .venv`
     - `.venv\Scripts\Activate.ps1`
2. Install dependencies:
   - `pip install -r requirements.txt`
3. Configure env:
   - `copy .env.example .env`
   - Fill API keys in `.env`
   - Set environment mode:
     - Testnet keys: `BYBIT_TESTNET=true` and `BYBIT_DEMO=false`
     - Demo-trading keys: `BYBIT_TESTNET=false` and `BYBIT_DEMO=true`
   - Optional timeout hardening:
     - `BYBIT_HTTP_TIMEOUT=30`
     - `BYBIT_HTTP_RETRIES=3`
     - `BYBIT_RETRY_BACKOFF_SECONDS=1.5`
4. Start dashboard server:
   - `python main.py`
5. Open dashboard:
   - `http://127.0.0.1:8000`
6. Click **Start Bot (runs 1s test trade)** in the dashboard.

## Testnet (Demo) First

1. In `.env`, set:
   - `BYBIT_TESTNET=true`
   - `BYBIT_DEMO=false`
2. Use testnet API key/secret from Bybit testnet.
3. Run `python main.py`
4. In dashboard, click **Start Bot (runs 1s test trade)**.
5. Check:
   - Dashboard orders/logs update.
   - Bybit testnet positions and executions match.

## Live Trading

1. Stop bot.
2. In `.env`:
   - Set `BYBIT_TESTNET=false`
   - Set `BYBIT_DEMO=false`
   - Set live API key/secret (with trade permissions).
3. Start again:
   - `python main.py`
4. In dashboard, click **Start Bot (runs 1s test trade)**.
5. Keep bot host machine stable (no sleep, stable internet).

## Dashboard Data Includes

- Current positions
- Recent orders/trades
- Total notional deployed
- Total expected risk
- Open position count
- Current unrealised PnL
- Bot logs and decision trace
- Start/Stop bot controls from dashboard
- Browser sound alert on new picked trades

## Notes

- This bot uses market entry + `set_trading_stop` TP/SL on Bybit.
- Trade history shown is bot-recorded local history in SQLite (`bot_data.db`).
- Always start on demo/testnet before live.
- Bybit testnet can be intermittently slow; request retry/backoff is included.
