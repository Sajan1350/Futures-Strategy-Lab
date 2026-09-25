"""
Technical indicator calculations for ATR Trend Follower (5-min).

All functions accept a pandas DataFrame with OHLCV + timestamp columns
and return a new DataFrame with indicator columns appended.
"""

import pandas as pd
import pandas_ta as ta
import numpy as np
import config as cfg


def add_5m_indicators(df: pd.DataFrame, df_1h: pd.DataFrame) -> pd.DataFrame:
    """
    Compute the 5-minute indicator columns the platform provides.

    Requires: open, high, low, close, volume, timestamp columns.
    df_1h is used for the 1H 50-EMA higher-timeframe filter.
    """
    df = df.copy()
    c = df["close"]
    h = df["high"]
    lo = df["low"]
    v = df["volume"]

    # ── EMAs ──
    df["ema_9"] = ta.ema(c, length=cfg.EMA_FAST_LEN)
    df["ema_20"] = ta.ema(c, length=cfg.EMA_SLOW_LEN)

    # ── ATR (two lengths for volatility expansion check + SL) ──
    df["atr_fast"] = ta.atr(h, lo, c, length=cfg.ATR_FAST_LEN)
    df["atr_slow"] = ta.atr(h, lo, c, length=cfg.ATR_SLOW_LEN)

    # ── Keltner Channels (using ATR_LEN = KC_ATR_LEN for the KC internal ATR) ──
    kc1 = ta.kc(h, lo, c, length=cfg.KC_LEN, scalar=cfg.KC_MULT1,
                mamode="ema", atrlength=cfg.KC_ATR_LEN)
    kc2 = ta.kc(h, lo, c, length=cfg.KC_LEN, scalar=cfg.KC_MULT2,
                mamode="ema", atrlength=cfg.KC_ATR_LEN)
    df["kc_basis"] = kc1[f"KCBe_{cfg.KC_LEN}_{cfg.KC_MULT1}"]
    df["kc_upper1"] = kc1[f"KCUe_{cfg.KC_LEN}_{cfg.KC_MULT1}"]
    df["kc_lower1"] = kc1[f"KCLe_{cfg.KC_LEN}_{cfg.KC_MULT1}"]
    df["kc_upper2"] = kc2[f"KCUe_{cfg.KC_LEN}_{cfg.KC_MULT2}"]
    df["kc_lower2"] = kc2[f"KCLe_{cfg.KC_LEN}_{cfg.KC_MULT2}"]

    # ── Take-profit band (separate from KC1/KC2 so it can be swept) ──
    if cfg.KC_MULT_TP == cfg.KC_MULT1:
        df["kc_upper_tp"] = df["kc_upper1"]
        df["kc_lower_tp"] = df["kc_lower1"]
    elif cfg.KC_MULT_TP == cfg.KC_MULT2:
        df["kc_upper_tp"] = df["kc_upper2"]
        df["kc_lower_tp"] = df["kc_lower2"]
    else:
        kc_tp = ta.kc(h, lo, c, length=cfg.KC_LEN, scalar=cfg.KC_MULT_TP,
                      mamode="ema", atrlength=cfg.KC_ATR_LEN)
        df["kc_upper_tp"] = kc_tp[f"KCUe_{cfg.KC_LEN}_{cfg.KC_MULT_TP}"]
        df["kc_lower_tp"] = kc_tp[f"KCLe_{cfg.KC_LEN}_{cfg.KC_MULT_TP}"]

    # ── KC Bandwidth Squeeze (range detection) ──
    df["kc_bw"] = df["kc_upper1"] - df["kc_lower1"]
    df["kc_bw_ma"] = df["kc_bw"].rolling(cfg.KC_BW_LOOKBACK).mean()
    df["kc_squeeze"] = False
    if cfg.USE_KC_BANDWIDTH:
        mask = df["kc_bw_ma"] > 0
        df.loc[mask, "kc_squeeze"] = (
            df.loc[mask, "kc_bw"] / df.loc[mask, "kc_bw_ma"] <= cfg.KC_BW_THRESH
        )

    # ── Flat KC Slope (range detection) ──
    kc_slope_abs = (df["kc_basis"] - df["kc_basis"].shift(cfg.KC_SLOPE_BARS)).abs()
    df["kc_slope_flat"] = False
    if cfg.USE_KC_SLOPE:
        valid = kc_slope_abs.notna() & df["atr_slow"].notna()
        df.loc[valid, "kc_slope_flat"] = (
            kc_slope_abs[valid] < cfg.KC_SLOPE_MIN_ATR * df.loc[valid, "atr_slow"]
        )

    # ── Range regime (either detector triggers) ──
    df["is_range"] = df["kc_squeeze"] | df["kc_slope_flat"]

    # ── VWAP (intraday reset) ──
    df["vwap"] = _calc_vwap(df)

    # ── 20-bar volume SMA (for volume confirmation filter) ──
    df["volume_ma"] = v.rolling(cfg.VOLUME_MA_LEN).mean()

    # ── Candle body metrics ──
    df["body_top"] = df[["open", "close"]].max(axis=1)
    df["body_bot"] = df[["open", "close"]].min(axis=1)
    df["body_mid"] = (df["open"] + df["close"]) / 2

    # ── 1H 50-EMA (higher-timeframe trend filter) ──
    df["ema_50_1h"] = _map_htf_column(df, df_1h, "ema_50_1h")

    return df


def add_1h_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate 1H-only indicators (50 EMA for HTF filter)."""
    df = df.copy()
    df["ema_50_1h"] = ta.ema(df["close"], length=cfg.EMA_50_1H_LEN)
    return df


def add_daily_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate daily indicators (yesterday's high/low/close for range bias)."""
    df = df.copy()
    # Yesterday's values (shift by 1 day)
    df["yest_high"] = df["high"].shift(1)
    df["yest_low"] = df["low"].shift(1)
    df["yest_close"] = df["close"].shift(1)
    return df


# ────────────────────────────────────────────────────────────────
#  Helpers
# ────────────────────────────────────────────────────────────────

def _calc_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Intraday VWAP that resets at CME Globex session open (6:00 PM ET).
    Vectorised for multi-million-bar backtests.
    """
    hlc3 = (df["high"] + df["low"] + df["close"]) / 3

    ts_et = df["timestamp"].dt.tz_convert("America/New_York")
    hour = ts_et.dt.hour
    session_date = ts_et.dt.date.copy()
    mask = hour >= 18
    shifted = (ts_et[mask] + pd.Timedelta(days=1)).dt.date
    session_date.loc[mask] = shifted

    tp_vol = hlc3 * df["volume"]
    cum_tp = tp_vol.groupby(session_date).cumsum()
    cum_vol = df["volume"].groupby(session_date).cumsum()
    vwap = cum_tp / cum_vol.replace(0, float("nan"))
    return vwap


def _map_htf_column(df_lf: pd.DataFrame, df_htf: pd.DataFrame, col: str) -> pd.Series:
    """
    Forward-fill a higher-timeframe indicator column onto lower-timeframe bars.
    Uses pd.merge_asof for O(n log n) vectorised alignment.
    """
    if df_htf is None or df_htf.empty or col not in df_htf.columns:
        return pd.Series(float("nan"), index=df_lf.index)

    htf = df_htf[["timestamp", col]].dropna().copy().sort_values("timestamp")
    lf = df_lf[["timestamp"]].copy().sort_values("timestamp")
    lf["_orig_idx"] = df_lf.index

    merged = pd.merge_asof(lf, htf, on="timestamp", direction="backward")
    merged = merged.set_index("_orig_idx")
    return merged[col].reindex(df_lf.index)
