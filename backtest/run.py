"""
CLI entry point for running backtests.

Usage:
    python -m backtest.run --list
    python -m backtest.run --strategy MyStrategy --symbol MGC
    python -m backtest.run --strategy MyStrategy --all
    python -m backtest.run --strategy MyStrategy --symbol MGC                            --start 2022-01-01 --end 2025-12-31

The strategy is looked up in the registry by name, so a new strategy
becomes runnable as soon as it is decorated with @register.
"""

import argparse
import logging
import sys
import time
from pathlib import Path
from collections import Counter

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.engine import run_backtest, BacktestResult
from backtest.metrics import compute_metrics
import strategies

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
log = logging.getLogger("backtest")

SYMBOLS = ["MGC", "MNQ", "MES", "MCL"]
RESULTS_DIR = Path(__file__).resolve().parent / "results"


def trades_to_df(trades: list) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()
    records = []
    for t in trades:
        records.append({
            "symbol": t.symbol,
            "strategy": t.strategy,
            "direction": "LONG" if t.direction == 1 else "SHORT",
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "entry_price": round(t.entry_price, 4),
            "exit_price": round(t.exit_price, 4),
            "stop_loss": round(t.stop_loss, 4),
            "atr": round(t.atr_at_entry, 4),
            "size": t.size,
            "pnl": round(t.pnl, 2),
            "pnl_gross": round(t.pnl_gross, 2),
            "commission": round(t.commission, 2),
            "reason": t.reason,
            "bars_held": t.bars_held,
        })
    return pd.DataFrame(records)


def print_exit_breakdown(trades):
    """Print exit reason breakdown."""
    if not trades:
        return
    by_reason = {}
    for t in trades:
        if t.reason not in by_reason:
            by_reason[t.reason] = {"n": 0, "pnls": []}
        by_reason[t.reason]["n"] += 1
        by_reason[t.reason]["pnls"].append(t.pnl)

    print(f"\n  Exit reason breakdown:")
    print(f"  {'reason':<16} {'n':>6}  {'avg_pnl':>10}  {'total':>14}  {'avg_bars':>9}")
    print(f"  {'-'*60}")

    # Also compute avg bars per reason
    bars_by_reason = {}
    for t in trades:
        bars_by_reason.setdefault(t.reason, []).append(t.bars_held)

    for reason in sorted(by_reason, key=lambda r: -abs(sum(by_reason[r]["pnls"]))):
        stats = by_reason[reason]
        total = sum(stats["pnls"])
        avg = total / stats["n"]
        avg_bars = sum(bars_by_reason[reason]) / len(bars_by_reason[reason])
        print(f"  {reason:<16} {stats['n']:>6d}  ${avg:>+9.2f}  ${total:>+13,.0f}  {avg_bars:>8.1f}")


def main():
    parser = argparse.ArgumentParser(description="Backtest a registered strategy")
    parser.add_argument("--strategy", type=str,
                        help="Registered strategy name (see --list)")
    parser.add_argument("--list", action="store_true",
                        help="List registered strategies and exit")
    parser.add_argument("--symbol", type=str, help="Instrument symbol")
    parser.add_argument("--all", action="store_true", help="Run all 4 instruments")
    parser.add_argument("--start", "--from", dest="start", type=str, default=None)
    parser.add_argument("--end", "--to", dest="end", type=str, default=None)
    parser.add_argument("--commission", type=float, default=1.50)
    parser.add_argument("--adjusted", action="store_true",
                        help="Use Panama back-adjusted continuous series")
    args = parser.parse_args()

    if args.list:
        names = strategies.available()
        print("Registered strategies:")
        for n in names:
            cls = strategies.REGISTRY[n]
            doc = (cls.__doc__ or "").strip().splitlines()
            print(f"  {n:<24} {doc[0] if doc else ''}")
        if not names:
            print("  (none yet - add one in strategies/, see strategies/template.py)")
        return

    if not args.strategy:
        parser.error("--strategy is required (or use --list)")
    try:
        strat_cls = strategies.get(args.strategy)
    except KeyError as e:
        parser.error(str(e))
    strat_name = strat_cls.name

    if not args.symbol and not args.all:
        parser.error("Specify --symbol or --all")

    symbols = SYMBOLS if args.all else [args.symbol.upper()]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 60)
    print(f"  BACKTEST: {strat_name} (5-min)")
    print("=" * 60)
    print(f"  Instruments : {', '.join(symbols)}")
    print(f"  Date range  : {args.start or 'all'} -> {args.end or 'all'}")
    print(f"  Commission  : ${args.commission:.2f} / RT / contract")
    print("=" * 60 + "\n")

    all_results = {}
    total_start = time.time()

    for sym in symbols:
        sym_start = time.time()
        try:
            result = run_backtest(
                symbol=sym,
                strategies=[strat_cls()],
                start=args.start, end=args.end,
                commission=args.commission,
                adjusted=args.adjusted,
            )
            metrics = compute_metrics(result.trades)
            all_results[sym] = (result, metrics)

            print(metrics.summary(symbol=sym, strategy=strat_name))
            print_exit_breakdown(result.trades)

            # Save
            df_trades = trades_to_df(result.trades)
            if not df_trades.empty:
                csv_path = RESULTS_DIR / f"{sym}_{strat_name}_trades.csv"
                df_trades.to_csv(csv_path, index=False)
                log.info(f"Saved {len(df_trades)} trades -> {csv_path}")

            txt_path = RESULTS_DIR / f"{sym}_{strat_name}_metrics.txt"
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(f"Backtest: {sym} | {strat_name} (5-min)\n")
                f.write(f"Period: {result.start_date} -> {result.end_date}\n")
                f.write(metrics.summary(symbol=sym, strategy=strat_name))

            elapsed = time.time() - sym_start
            log.info(f"[{sym}] completed in {elapsed:.1f}s")

        except Exception as e:
            log.error(f"[{sym}] FAILED: {e}", exc_info=True)

    # Portfolio summary
    if len(all_results) > 1:
        all_trades = []
        for sym, (result, _) in all_results.items():
            all_trades.extend(result.trades)
        if all_trades:
            all_trades.sort(key=lambda t: str(t.exit_time))
            pf_metrics = compute_metrics(all_trades)
            print("\n" + "#" * 60)
            print("  PORTFOLIO SUMMARY (ALL INSTRUMENTS)")
            print("#" * 60)
            print(pf_metrics.summary(symbol="Portfolio", strategy=strat_name))
            print_exit_breakdown(all_trades)

    total_elapsed = time.time() - total_start
    print(f"\nTotal time: {total_elapsed:.1f}s")


if __name__ == "__main__":
    main()
