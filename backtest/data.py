"""
Data loader for backtesting.

Reads per-instrument parquet files from data/parquet/ and resamples
1-minute bars into 1-hour and daily bars so that the full indicator
stack (add_1m_indicators, add_1h_indicators, add_daily_indicators)
can run identically to the live bot.
"""

import logging
from pathlib import Path

import pandas as pd
import numpy as np

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "parquet"


def load_1m(symbol: str, start: str = None, end: str = None,
            adjusted: bool = False) -> pd.DataFrame:
    """
    Load 1-minute OHLCV bars from parquet.

    Returns DataFrame with columns:
        timestamp (tz-aware UTC), open, high, low, close, volume
    sorted by timestamp with integer index.

    If adjusted=True, loads {SYMBOL}_adj.parquet (Panama back-adjusted)
    instead of the raw stitched series.
    """
    suffix = "_adj" if adjusted else ""
    path = DATA_DIR / f"{symbol.upper()}{suffix}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"No parquet file at {path}")

    df = pd.read_parquet(path)

    # Normalise index → column
    if "timestamp" not in df.columns:
        df = df.reset_index()
        if "ts_event" in df.columns:
            df = df.rename(columns={"ts_event": "timestamp"})
        # If index was named 'timestamp' it becomes a column after reset_index
    if df.columns[0] == df.index.name:
        df = df.reset_index(drop=True)

    # Ensure timestamp is tz-aware UTC
    if not hasattr(df["timestamp"].dtype, "tz") or df["timestamp"].dt.tz is None:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    else:
        df["timestamp"] = df["timestamp"].dt.tz_convert("UTC")

    df = df.sort_values("timestamp").reset_index(drop=True)

    # Filter date range
    if start:
        df = df[df["timestamp"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df["timestamp"] <= pd.Timestamp(end, tz="UTC")]

    df = df.reset_index(drop=True)

    # Ensure numeric columns
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Drop rows with NaN prices
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)

    log.info(f"Loaded {symbol}: {len(df):,} bars  "
             f"{df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")
    return df


def resample_to_5m(df_1m: pd.DataFrame) -> pd.DataFrame:
    """Resample 1-minute bars to 5-minute bars."""
    df = df_1m.set_index("timestamp")
    bars_5m = df.resample("5min").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open"])
    bars_5m = bars_5m.reset_index()
    if "timestamp" not in bars_5m.columns and bars_5m.index.name == "timestamp":
        bars_5m = bars_5m.reset_index()
    return bars_5m.reset_index(drop=True)


def resample_to_1h(df_1m: pd.DataFrame) -> pd.DataFrame:
    """Resample 1-minute bars to 1-hour bars."""
    df = df_1m.set_index("timestamp")
    hourly = df.resample("1h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open"])
    hourly = hourly.reset_index().rename(columns={"index": "timestamp"})
    if "timestamp" not in hourly.columns and hourly.index.name == "timestamp":
        hourly = hourly.reset_index()
    return hourly.reset_index(drop=True)


def resample_to_daily(df_1m: pd.DataFrame) -> pd.DataFrame:
    """
    Resample 1-minute bars to daily bars.

    Uses CME session boundaries: session starts 18:00 ET previous day.
    For simplicity we use calendar-day grouping which is close enough
    for indicator computation (EMA-20 daily, pivots).
    """
    df = df_1m.set_index("timestamp")
    daily = df.resample("1D").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open"])
    daily = daily.reset_index().rename(columns={"index": "timestamp"})
    if "timestamp" not in daily.columns and daily.index.name == "timestamp":
        daily = daily.reset_index()
    return daily.reset_index(drop=True)
