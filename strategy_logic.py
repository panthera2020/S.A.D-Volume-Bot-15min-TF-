from dataclasses import dataclass

import pandas as pd

from config import BotConfig
from indicators import dmi_adx, rsi


@dataclass
class StrategySignal:
    symbol: str
    side: str
    entry_price: float
    stop_loss: float
    take_profit: float
    qty: float
    expected_risk_usd: float
    reason: str


def enrich_indicators(df: pd.DataFrame, cfg: BotConfig) -> pd.DataFrame:
    out = df.copy()
    out["upper_dc"] = out["high"].rolling(cfg.donchian_len).max()
    out["lower_dc"] = out["low"].rolling(cfg.donchian_len).min()
    out["mid_dc"] = (out["upper_dc"] + out["lower_dc"]) / 2.0
    out["rsi"] = rsi(out["close"], cfg.rsi_len)
    dmi = dmi_adx(out, cfg.adx_len)
    out["plus_di"] = dmi["plus_di"]
    out["minus_di"] = dmi["minus_di"]
    out["adx"] = dmi["adx"]
    out["vol_ma"] = out["volume"].rolling(20).mean()
    out["vol_spike"] = out["volume"] > (out["vol_ma"] * cfg.volume_multiplier)
    out["swing_low"] = out["low"].rolling(cfg.swing_lookback).min()
    out["swing_high"] = out["high"].rolling(cfg.swing_lookback).max()
    return out


def build_signal(symbol: str, df: pd.DataFrame, cfg: BotConfig, qty: float) -> StrategySignal | None:
    if len(df) < max(cfg.donchian_len + 2, 40):
        return None

    row = df.iloc[-1]
    prev = df.iloc[-2]
    close = float(row["close"])

    bull_trend = close > float(row["mid_dc"])
    bear_trend = close < float(row["mid_dc"])

    long_condition = bull_trend and close > float(prev["upper_dc"]) and float(row["rsi"]) > 40 and bool(row["vol_spike"])
    short_condition = bear_trend and close < float(prev["lower_dc"]) and float(row["rsi"]) < 60 and bool(row["vol_spike"])

    if not long_condition and not short_condition:
        return None

    if long_condition:
        sl = float(row["swing_low"])
        tp = close + (close - sl) * cfg.risk_rr
        expected_risk = abs(close - sl) * qty
        return StrategySignal(
            symbol=symbol,
            side="Buy",
            entry_price=close,
            stop_loss=sl,
            take_profit=tp,
            qty=qty,
            expected_risk_usd=expected_risk,
            reason="Long condition met: trend + Donchian breakout + RSI + volume spike",
        )

    sl = float(row["swing_high"])
    tp = close - (sl - close) * cfg.risk_rr
    expected_risk = abs(sl - close) * qty
    return StrategySignal(
        symbol=symbol,
        side="Sell",
        entry_price=close,
        stop_loss=sl,
        take_profit=tp,
        qty=qty,
        expected_risk_usd=expected_risk,
        reason="Short condition met: trend + Donchian breakdown + RSI + volume spike",
    )
