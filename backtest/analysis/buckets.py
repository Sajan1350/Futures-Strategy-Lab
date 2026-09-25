"""
Bucketed performance analysis — strategy-agnostic.

The question this answers is always the same shape: *does some measurable
property of the entry bar separate winners from losers?* Slice entries by
that property, then compare trades / win rate / profit factor / PnL across
the slices.

Typical use:

    from backtest.engine import run_backtest
    from backtest.analysis import (
        aggregate_entries, join_entry_features, quantile_report,
        category_report, print_table,
    )

    res = run_backtest("MGC", [MyStrategy()])
    df  = aggregate_entries(res.trades)
    df  = join_entry_features(df, res.df_5m, ["atr_fast", "atr_slow", "hour_pt"])
    df["atr_expansion"] = df["atr_fast"] / df["atr_slow"]

    print_table("ATR expansion", quantile_report(df, "atr_expansion"), "bucket")
    print_table("Hour of day",   category_report(df, "hour_pt"),       "hour_pt")

A caution worth repeating: buckets computed on the same history you then
validate on are in-sample. A split that looks strong here has to survive
an out-of-sample period before it means anything.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def aggregate_entries(trades: list) -> pd.DataFrame:
    """Collapse trade records into one row per entry.

    A scale-out strategy emits several trade records per entry (TP1, TP2,
    the stop on the remainder...). Analysing those separately double-counts
    entries and understates per-entry variance, so sum them back up by
    (symbol, direction, entry_time).

    Returns columns: symbol, direction, entry_time, pnl, exits.
    """
    if not trades:
        return pd.DataFrame(
            columns=["symbol", "direction", "entry_time", "pnl", "exits"]
        )

    raw = pd.DataFrame([{
        "symbol": t.symbol,
        "direction": t.direction,
        "entry_time": pd.Timestamp(t.entry_time),
        "pnl": t.pnl,
    } for t in trades])

    return (raw.groupby(["symbol", "direction", "entry_time"], as_index=False)
               .agg(pnl=("pnl", "sum"), exits=("pnl", "size")))


def join_entry_features(entries: pd.DataFrame, df_5m: pd.DataFrame,
                        columns: list[str]) -> pd.DataFrame:
    """Attach entry-bar values of `columns` to each entry row.

    `df_5m` is the indicator frame returned on BacktestResult.df_5m, so any
    column the engine computed — indicator, runtime feature, or one you
    added yourself — can be pulled in by name.
    """
    if entries.empty:
        return entries

    missing = [c for c in columns if c not in df_5m.columns]
    if missing:
        raise KeyError(
            f"columns not present in df_5m: {missing}. "
            f"Available: {sorted(df_5m.columns)}"
        )

    feats = df_5m[["timestamp", *columns]].set_index("timestamp")
    return entries.merge(feats, left_on="entry_time", right_index=True, how="left")


def direction_adjust(entries: pd.DataFrame, column: str) -> pd.Series:
    """Sign-flip a directional feature so positive always means 'with the
    trade'. A raw EMA spread is negative on a valid short; comparing it
    against longs without flipping mixes two different things."""
    return entries[column] * entries["direction"]


def bucket_stats(g: pd.DataFrame) -> pd.Series:
    """trades / wins / win rate / profit factor / total PnL / avg PnL."""
    n = len(g)
    wins = int((g["pnl"] > 0).sum())
    gross_win = g.loc[g["pnl"] > 0, "pnl"].sum()
    gross_loss = -g.loc[g["pnl"] <= 0, "pnl"].sum()
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    return pd.Series({
        "trades": n,
        "wins": wins,
        "win_rate": (wins / n * 100) if n else 0.0,
        "profit_factor": pf,
        "total_pnl": g["pnl"].sum(),
        "avg_pnl": g["pnl"].mean() if n else 0.0,
    })


def quantile_report(entries: pd.DataFrame, feature: str,
                    nbuckets: int = 5) -> pd.DataFrame:
    """Split entries into `nbuckets` equal-count buckets of `feature`.

    Equal-count (not equal-width) so every bucket carries the same
    statistical weight. Buckets are labelled with their numeric range,
    e.g. "Q3 [0.12,0.18]", and returned in ascending order.
    """
    if entries.empty or feature not in entries:
        return pd.DataFrame()

    df = entries.dropna(subset=[feature]).copy()
    if df.empty:
        return pd.DataFrame()

    try:
        df["_q"] = pd.qcut(df[feature], q=nbuckets, duplicates="drop")
    except ValueError:
        return pd.DataFrame()

    cats = df["_q"].cat.categories
    labels = {c: f"Q{i+1} [{c.left:.2f},{c.right:.2f}]" for i, c in enumerate(cats)}
    df["bucket"] = df["_q"].map(labels)

    tbl = (df.groupby("bucket", observed=True)[["pnl"]]
             .apply(bucket_stats)
             .reset_index())
    order = {labels[c]: i for i, c in enumerate(cats)}
    return (tbl.assign(_o=tbl["bucket"].map(order))
               .sort_values("_o")
               .drop(columns="_o")
               .reset_index(drop=True))


def category_report(entries: pd.DataFrame, column: str) -> pd.DataFrame:
    """Same statistics grouped by a discrete column (hour, symbol, weekday)."""
    if entries.empty or column not in entries:
        return pd.DataFrame()
    tbl = (entries.groupby(column)[["pnl"]]
                  .apply(bucket_stats)
                  .reset_index())
    return tbl.sort_values(column).reset_index(drop=True)


def print_table(title: str, tbl: pd.DataFrame, label_col: str) -> None:
    """Print a report table in a fixed-width layout."""
    print(f"\n-- {title} --")
    if tbl.empty:
        print("   (no data)")
        return
    print(f"  {label_col:>24}  {'trades':>7}  {'wins':>5}  {'WR%':>6}  "
          f"{'PF':>6}  {'avg PnL':>10}  {'total PnL':>14}")
    print(f"  {'-' * 92}")
    for _, r in tbl.iterrows():
        pf = r["profit_factor"]
        pf_str = f"{pf:>6.2f}" if np.isfinite(pf) else "   inf"
        print(f"  {str(r[label_col]):>24}  {int(r['trades']):>7d}  "
              f"{int(r['wins']):>5d}  {r['win_rate']:>5.1f}  "
              f"{pf_str}  ${r['avg_pnl']:>+8.2f}  ${r['total_pnl']:>+12,.0f}")
