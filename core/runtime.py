"""
Shared runtime feature computation used by both the backtest engine and
live trading. Keeping the logic here guarantees live trading sees the
exact same session mask and derived state the backtest engine saw for
the same bar.

Two layers:

  * Always computed (strategy-agnostic): PT hour, session mask,
    session-half split, Globex session date, EMA-cross freshness.

  * Opt-in (`with_locked_bias=True`): the locked-per-session-half
    directional bias. This is one specific way to define daily bias,
    not a universal truth - it is off by default so a new strategy is
    not silently inheriting it. Turn it on only if you want exactly
    this construct; otherwise compute your own bias in the strategy.
"""

from __future__ import annotations

import pandas as pd
import numpy as np

import config as cfg


def bars_to_df(bars: list) -> pd.DataFrame:
    """Convert a list of broker bar dicts into a DataFrame.

    Broker responses vary in capitalization; we normalise to lowercase OHLCV +
    a timezone-aware `timestamp` column (UTC).
    """
    if not bars:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    rows = []
    for b in bars:
        ts = b.get("t") or b.get("timestamp") or b.get("time")
        rows.append({
            "timestamp": pd.to_datetime(ts, utc=True),
            "open":   float(b.get("o") or b.get("open") or 0),
            "high":   float(b.get("h") or b.get("high") or 0),
            "low":    float(b.get("l") or b.get("low") or 0),
            "close":  float(b.get("c") or b.get("close") or 0),
            "volume": float(b.get("v") or b.get("volume") or 0),
        })
    df = pd.DataFrame(rows)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    return df


def precompute_runtime_features(df_5m: pd.DataFrame, df_daily: pd.DataFrame,
                                ic: dict,
                                with_locked_bias: bool = False) -> pd.DataFrame:
    """Pre-compute session mask, PT hour, session date and EMA-cross
    freshness on a 5-minute indicator frame. Optionally adds the
    locked-per-half directional bias.

    Input requirements:
      * `df_5m` must already have `ema_9`, `ema_20`, `atr_slow` columns
        produced by `add_5m_indicators`.
      * `df_5m["timestamp"]` must be timezone-aware UTC.

    Columns always added:
        hour_pt, in_session, is_first_half, session_date,
        bars_since_bull_cross, bars_since_bear_cross

    Columns added when `with_locked_bias=True`:
        fh_high/low/close, prev_sh_high/low/close, bias_direction

    Locked bias design (opt-in):
      * Decided at the first in-session bar of each half, then held for
        the rest of the half - no mid-half flips on pullbacks.
      * Anchor = close of the PRECEDING like-for-like half:
          - Today's first half  -> yesterday's second-half close
          - Today's second half -> today's first-half close
      * Dead zone = DEAD_ZONE_ATR_MULT * ATR(slow) sampled at the first
        bar of the half (ATR-based, stable across vol regimes).
    """
    df_5m = df_5m.copy()

    # Drop any columns this function produces via merges, so that running it
    # a second time (as bot_manager does on every new bar) doesn't collide
    # with the previous run's output. Without this, pandas suffixes the
    # second merge with _x/_y and later lookups of e.g. `prev_sh_close` fail.
    stale_merge_cols = [
        "fh_high", "fh_low", "fh_close",
        "prev_sh_high", "prev_sh_low", "prev_sh_close",
        "locked_bias",
    ]
    df_5m = df_5m.drop(columns=[c for c in stale_merge_cols if c in df_5m.columns])

    # ── Session mask + session-half split ──
    pt_times = df_5m["timestamp"].dt.tz_convert("America/Vancouver")
    df_5m["hour_pt"] = pt_times.dt.hour.astype(np.int8)

    if cfg.USE_SESSION:
        start_pt = ic.get("session_start_pt", cfg.SESSION_START_PT)
        end_pt = ic.get("session_end_pt", cfg.SESSION_END_PT)
        minutes = pt_times.dt.hour * 60 + pt_times.dt.minute
        s_h, s_m = map(int, start_pt.split(":"))
        e_h, e_m = map(int, end_pt.split(":"))
        start_mins = s_h * 60 + s_m
        end_mins = e_h * 60 + e_m
        if start_mins <= end_mins:
            df_5m["in_session"] = (minutes >= start_mins) & (minutes <= end_mins)
        else:
            df_5m["in_session"] = (minutes >= start_mins) | (minutes <= end_mins)
        mid_mins = (start_mins + end_mins) // 2
        df_5m["is_first_half"] = minutes < mid_mins
    else:
        df_5m["in_session"] = True
        df_5m["is_first_half"] = True

    # ── Session date (Globex: 18:00+ ET rolls to next session day) ──
    ts_et = df_5m["timestamp"].dt.tz_convert("America/New_York")
    hour_et = ts_et.dt.hour
    session_date = ts_et.dt.date.copy()
    mask_evening = hour_et >= 18
    shifted_dates = (ts_et[mask_evening] + pd.Timedelta(days=1)).dt.date
    session_date.loc[mask_evening] = shifted_dates
    df_5m["session_date"] = session_date.values

    # ── Locked directional bias (opt-in) ──
    # Off by default: this is one strategy's definition of bias, not a
    # universal feature. See the module docstring.
    if with_locked_bias:
        # ── Per-session aggregates: first and second half ──
        fh_mask = df_5m["is_first_half"] & df_5m["in_session"]
        sh_mask = (~df_5m["is_first_half"]) & df_5m["in_session"]

        fh_agg = (df_5m.loc[fh_mask]
                  .groupby("session_date")
                  .agg(fh_high=("high", "max"),
                       fh_low=("low", "min"),
                       fh_close=("close", "last")))
        sh_agg = (df_5m.loc[sh_mask]
                  .groupby("session_date")
                  .agg(sh_high=("high", "max"),
                       sh_low=("low", "min"),
                       sh_close=("close", "last")))

        # "Yesterday's second half" = shift second-half aggregate by 1 session day
        prev_sh = sh_agg.sort_index().shift(1).rename(columns={
            "sh_high":  "prev_sh_high",
            "sh_low":   "prev_sh_low",
            "sh_close": "prev_sh_close",
        })

        df_5m = df_5m.merge(fh_agg, on="session_date", how="left")
        df_5m = df_5m.merge(prev_sh, on="session_date", how="left")

        # ── Lock bias per (session_date, is_first_half) ──
        in_sess_df = df_5m[df_5m["in_session"]].copy()
        first_bars = (in_sess_df.groupby(["session_date", "is_first_half"])
                      .head(1)
                      .copy())

        is_fh_first = first_bars["is_first_half"].values
        ref_close = np.where(is_fh_first,
                             first_bars["prev_sh_close"].values,
                             first_bars["fh_close"].values)
        atr_here = first_bars["atr_slow"].values
        close_here = first_bars["close"].values
        dead_half = cfg.DEAD_ZONE_ATR_MULT * atr_here / 2.0

        locked = np.zeros(len(first_bars), dtype=np.int8)
        valid = ~(np.isnan(ref_close) | np.isnan(atr_here) | (atr_here <= 0))
        locked[valid & (close_here > ref_close + dead_half)] = 1
        locked[valid & (close_here < ref_close - dead_half)] = -1
        first_bars["locked_bias"] = locked

        locked_map = first_bars[["session_date", "is_first_half", "locked_bias"]]
        df_5m = df_5m.merge(locked_map, on=["session_date", "is_first_half"], how="left")
        df_5m["bias_direction"] = df_5m["locked_bias"].fillna(0).astype(np.int8)
        df_5m.loc[~df_5m["in_session"], "bias_direction"] = 0
        df_5m = df_5m.drop(columns=["locked_bias"])

    # ── EMA cross freshness ──
    ema_9 = df_5m["ema_9"].values
    ema_20 = df_5m["ema_20"].values
    bull_cross = np.zeros(len(df_5m), dtype=np.int32)
    bear_cross = np.zeros(len(df_5m), dtype=np.int32)
    bs_bull = 9999
    bs_bear = 9999
    for idx in range(1, len(df_5m)):
        e9_prev, e20_prev = ema_9[idx - 1], ema_20[idx - 1]
        e9_now, e20_now = ema_9[idx], ema_20[idx]
        if (e9_prev == e9_prev and e20_prev == e20_prev
                and e9_now == e9_now and e20_now == e20_now):
            if e9_prev <= e20_prev and e9_now > e20_now:
                bs_bull = 0
            if e9_prev >= e20_prev and e9_now < e20_now:
                bs_bear = 0
        bs_bull += 1
        bs_bear += 1
        bull_cross[idx] = bs_bull
        bear_cross[idx] = bs_bear
    df_5m["bars_since_bull_cross"] = bull_cross
    df_5m["bars_since_bear_cross"] = bear_cross

    return df_5m
