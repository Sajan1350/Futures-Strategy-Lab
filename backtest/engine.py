"""
Event-driven backtest engine (5-min bars). Strategy-agnostic.

The engine owns the bar loop, position accounting and risk enforcement.
It knows nothing about any particular strategy's rules - every entry,
exit and trail decision comes from the Strategy objects passed in.

Provides:
  - 5-min bar loop, with 1H/daily-derived columns available on each row
  - Multi-contract positions with arbitrary partial exits (scale-outs)
  - Per-strategy rolling buffer of completed entry-level PnL, exposed as
    ctx["entry_pnl_history"] for strategies that size off recent results
  - Risk enforcement: daily loss limit, consecutive-loss kill switches
  - Strategy-driven trailing stops
  - 1-tick entry/exit slippage

Each strategy runs in its own StrategySlot, so several strategies - or one
strategy in both directions - can trade an instrument independently.
"""

import logging
from collections import deque
from dataclasses import dataclass
from datetime import date as dt_date

import pandas as pd
import numpy as np

import config as cfg
from instruments import INSTRUMENT_DEFS
from core.indicators import add_5m_indicators, add_1h_indicators, add_daily_indicators
from core.risk import RiskManager
from core.runtime import precompute_runtime_features
from strategies import StrategySlot
from backtest.data import load_1m, resample_to_5m, resample_to_1h, resample_to_daily
from backtest.broker_sim import simulate_entry_fill, calc_trade_pnl, DEFAULT_COMMISSION

log = logging.getLogger(__name__)

WARMUP_BARS = 60  # 60 × 5min = 5 hours of warmup for indicators


@dataclass
class Trade:
    """Record of one completed (or partial) trade."""
    symbol: str
    strategy: str
    direction: int
    entry_time: object
    exit_time: object
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    atr_at_entry: float
    size: int           # number of contracts closed in this trade record
    pnl: float
    pnl_gross: float
    commission: float
    reason: str
    bars_held: int = 0


@dataclass
class BacktestResult:
    symbol: str
    strategy_name: str
    trades: list
    equity_curve: list
    start_date: str
    end_date: str
    total_bars: int
    df_5m: object = None  # optional: indicator frame for post-hoc analysis


def run_backtest(
    symbol: str,
    strategies: list = None,
    start: str = None,
    end: str = None,
    commission: float = DEFAULT_COMMISSION,
    adjusted: bool = False,
    with_locked_bias: bool = False,
) -> BacktestResult:
    ic = INSTRUMENT_DEFS.get(symbol.upper())
    if ic is None:
        raise ValueError(f"Unknown instrument: {symbol}")

    if not strategies:
        raise ValueError(
            "run_backtest() requires at least one Strategy instance. "
            "The engine is strategy-agnostic by design - pass the strategy "
            "you want to test, e.g. run_backtest('MGC', [MyStrategy()])."
        )
    strategy_label = (strategies[0].name if len(strategies) == 1 else "All")

    # ── Load 1m data ──
    log.info(f"[{symbol}] Loading 1m data...")
    df_1m = load_1m(symbol, start=start, end=end, adjusted=adjusted)
    if len(df_1m) < 1000:
        raise ValueError(f"Not enough 1m bars ({len(df_1m)}) for {symbol}.")

    # ── Resample ──
    log.info(f"[{symbol}] Resampling to 5m / 1h / daily...")
    df_5m = resample_to_5m(df_1m)
    df_1h = resample_to_1h(df_1m)
    df_daily = resample_to_daily(df_1m)

    # ── Compute indicators ──
    log.info(f"[{symbol}] Computing indicators...")
    df_1h = add_1h_indicators(df_1h)
    df_daily = add_daily_indicators(df_daily)
    df_5m = add_5m_indicators(df_5m, df_1h)

    # ── Pre-compute runtime columns ──
    log.info(f"[{symbol}] Pre-computing session / runtime columns...")
    df_5m = precompute_runtime_features(df_5m, df_daily, ic,
                                        with_locked_bias=with_locked_bias)

    total_bars = len(df_5m)
    log.info(f"[{symbol}] Running bar loop on {total_bars - WARMUP_BARS:,} 5-min bars...")

    # Suppress risk manager spam
    logging.getLogger("strategy.risk").setLevel(logging.ERROR)

    # ── Init per-strategy slots ──
    slots = {
        s.name: StrategySlot(strategy_name=s.name, symbol=symbol, size=cfg.DEFAULT_TRADE_SIZE)
        for s in strategies
    }

    risk_mgr = RiskManager(initial_equity=50_000.0)
    last_trade_day = None

    trades: list[Trade] = []
    equity = 0.0
    equity_curve: list[float] = []
    bars_in_trade: dict[str, int] = {s.name: 0 for s in strategies}
    # ── Per-entry aggregated PnL tracking for dynamic sizing ──
    # entry_running_pnl accumulates partials while a position is open.
    # On full close, the total is pushed to entry_pnl_history (last 4).
    entry_pnl_history: dict[str, deque] = {
        s.name: deque(maxlen=4) for s in strategies
    }
    entry_running_pnl: dict[str, float] = {s.name: 0.0 for s in strategies}

    tick = ic["tick_size"]
    pv = ic["point_value"]

    # ── Extract numpy arrays for fast access ──
    arrays = {col: df_5m[col].to_numpy() for col in df_5m.columns if col != "timestamp"}
    timestamps = df_5m["timestamp"].tolist()
    columns = [c for c in df_5m.columns if c != "timestamp"]

    # ── Bar loop ──
    for i in range(WARMUP_BARS, total_bars):
        atr_val = arrays["atr_slow"][i]
        if not atr_val or atr_val != atr_val:
            for slot in slots.values():
                if slot.flat:
                    slot.bars_since_exit += 1
            continue

        ts = timestamps[i]
        row = {col: arrays[col][i] for col in columns}
        row["timestamp"] = ts
        close = row["close"]

        # ── Day rollover ──
        bar_date = ts.date() if hasattr(ts, "date") else dt_date.today()
        if last_trade_day != bar_date:
            risk_mgr._daily_pnl = 0.0
            risk_mgr._kill_triggered = False
            risk_mgr._consec_losses = 0
            risk_mgr._consec_loss_total = 0.0
            risk_mgr._consec_pause_until = None
            risk_mgr._consec_pause_level = 0
            last_trade_day = bar_date

        # ── Daily loss limit force-close ──
        if risk_mgr.daily_pnl <= -ic["daily_loss_limit"]:
            for strat in strategies:
                slot = slots[strat.name]
                if not slot.flat:
                    exit_price = (close - tick) if slot.trade_dir == 1 else (close + tick)
                    _record_trade(trades, slot, strat.name, symbol, ts,
                                  exit_price, pv, commission, "DailyLossLimit",
                                  bars_in_trade[strat.name], slot.size)
                    equity += trades[-1].pnl
                    equity_curve.append(equity)
                    risk_mgr.record_trade(trades[-1].pnl)
                    entry_running_pnl[strat.name] += trades[-1].pnl
                    entry_pnl_history[strat.name].append(entry_running_pnl[strat.name])
                    entry_running_pnl[strat.name] = 0.0
                    slot.reset()
                    bars_in_trade[strat.name] = 0
            for slot in slots.values():
                if slot.flat:
                    slot.bars_since_exit += 1
            continue

        # ── Session / range / bias ──
        in_session = bool(row.get("in_session", True))
        range_blocked = bool(row.get("is_range", False))
        bias = int(row.get("bias_direction", 0))

        can_enter = (not risk_mgr.kill_triggered
                     and risk_mgr.daily_pnl > -ic["daily_loss_limit"])

        ctx = {
            "symbol": symbol,
            "inst_cfg": ic,
            "in_session": in_session,
            "range_blocked": range_blocked,
            "bias_direction": bias,
            "risk_mgr": risk_mgr,
            "can_enter": can_enter,
            "entry_pnl_history": entry_pnl_history,
        }

        # ── Per-strategy processing ──
        for strat in strategies:
            slot = slots[strat.name]
            strat.on_bar_start(slot, row, ctx)

            if not slot.flat:
                bars_in_trade[strat.name] += 1

                # 1. Trail stop
                new_sl = strat.update_trail(slot, row, ctx)
                if new_sl is not None:
                    slot.stop_loss = new_sl

                # 2. SL check (bar high/low, no bracket TP — strategy handles TP)
                sl_hit = _check_sl(row, slot.trade_dir, slot.stop_loss)
                if sl_hit:
                    if slot.trade_dir == 1:
                        exit_price = slot.stop_loss - tick  # slippage
                    else:
                        exit_price = slot.stop_loss + tick
                    size_closing = slot.size
                    # A strategy that moves its stop sets slot.extra["stop_label"]
                    # (e.g. "TrailStop", "BreakevenStop") so the trade log
                    # attributes the exit correctly. Default: a plain stop-out.
                    sl_reason = slot.extra.get("stop_label") or "StopLoss"
                    _record_trade(trades, slot, strat.name, symbol, ts,
                                  exit_price, pv, commission, sl_reason,
                                  bars_in_trade[strat.name], size_closing)
                    equity += trades[-1].pnl
                    equity_curve.append(equity)
                    risk_mgr.record_trade(trades[-1].pnl)
                    entry_running_pnl[strat.name] += trades[-1].pnl
                    entry_pnl_history[strat.name].append(entry_running_pnl[strat.name])
                    entry_running_pnl[strat.name] = 0.0
                    slot.reset()
                    bars_in_trade[strat.name] = 0
                    continue

                # 3. Strategy-driven exit (scale-out, target, time stop, ...)
                exit_sig = strat.evaluate_exit(slot, row, ctx)
                if exit_sig and exit_sig.should_exit:
                    close_size = exit_sig.size if exit_sig.size > 0 else slot.size
                    close_size = min(close_size, slot.size)

                    if exit_sig.exit_price > 0:
                        exit_price = exit_sig.exit_price
                    elif slot.trade_dir == 1:
                        exit_price = close - tick
                    else:
                        exit_price = close + tick

                    _record_trade(trades, slot, strat.name, symbol, ts,
                                  exit_price, pv, commission, exit_sig.reason,
                                  bars_in_trade[strat.name], close_size)
                    equity += trades[-1].pnl
                    equity_curve.append(equity)
                    risk_mgr.record_trade(trades[-1].pnl)
                    entry_running_pnl[strat.name] += trades[-1].pnl

                    if close_size >= slot.size:
                        # Full exit — finalize entry-level PnL into rolling buffer
                        entry_pnl_history[strat.name].append(entry_running_pnl[strat.name])
                        entry_running_pnl[strat.name] = 0.0
                        slot.reset()
                        bars_in_trade[strat.name] = 0
                    else:
                        # Partial exit — reduce position
                        slot.size -= close_size
                    continue

            else:
                # Flat: evaluate entry
                slot.bars_since_exit += 1
                entry = strat.evaluate_entry(slot, row, ctx)
                if entry is not None:
                    fill = simulate_entry_fill(
                        entry.reference_price or close,
                        entry.direction, tick,
                    )
                    slot.trade_dir = entry.direction
                    slot.entry_price = fill.fill_price
                    slot.entry_atr = entry.atr
                    slot.stop_loss = entry.stop_loss
                    slot.take_profit = entry.take_profit
                    slot.trail_stop = entry.trail_stop
                    slot.size = entry.size
                    slot.bars_since_exit = 0
                    slot.extra["entry_time"] = ts
                    bars_in_trade[strat.name] = 0

        # Progress logging
        if (i - WARMUP_BARS) % 100_000 == 0 and i > WARMUP_BARS:
            pct = (i - WARMUP_BARS) / (total_bars - WARMUP_BARS) * 100
            log.info(f"[{symbol}] {pct:.0f}% done — {len(trades)} trades, "
                     f"equity=${equity:+.2f}")

    # ── Force-close open positions ──
    last_ts = timestamps[-1]
    last_close = arrays["close"][-1]
    for strat in strategies:
        slot = slots[strat.name]
        if not slot.flat:
            exit_price = (last_close - tick) if slot.trade_dir == 1 else (last_close + tick)
            _record_trade(trades, slot, strat.name, symbol, last_ts,
                          exit_price, pv, commission, "EndOfData",
                          bars_in_trade[strat.name], slot.size)
            equity += trades[-1].pnl
            equity_curve.append(equity)
            entry_running_pnl[strat.name] += trades[-1].pnl
            entry_pnl_history[strat.name].append(entry_running_pnl[strat.name])
            entry_running_pnl[strat.name] = 0.0
            slot.reset()

    actual_start = timestamps[WARMUP_BARS]
    actual_end = timestamps[-1]

    log.info(f"[{symbol}] Backtest complete: {len(trades)} trades, equity=${equity:+.2f}")

    return BacktestResult(
        symbol=symbol,
        strategy_name=strategy_label,
        trades=trades,
        equity_curve=equity_curve,
        start_date=str(actual_start.date()) if hasattr(actual_start, "date") else str(actual_start),
        end_date=str(actual_end.date()) if hasattr(actual_end, "date") else str(actual_end),
        total_bars=total_bars - WARMUP_BARS,
        df_5m=df_5m,
    )


# ────────────────────────────────────────────────────────────────
#  Helpers
# ────────────────────────────────────────────────────────────────

def _check_sl(row: dict, direction: int, stop_loss: float) -> bool:
    """Check if stop-loss was hit on this bar."""
    if direction == 1:
        return row["low"] <= stop_loss
    else:
        return row["high"] >= stop_loss


def _record_trade(trades: list, slot: StrategySlot, strat_name: str,
                  symbol: str, ts, exit_price: float, pv: float,
                  commission: float, reason: str, bars_held: int,
                  close_size: int):
    """Record a trade (full or partial exit)."""
    gross = (exit_price - slot.entry_price) * slot.trade_dir * close_size * pv
    comm = commission * close_size
    pnl = gross - comm
    trades.append(Trade(
        symbol=symbol,
        strategy=strat_name,
        direction=slot.trade_dir,
        entry_time=slot.extra.get("entry_time", ts),
        exit_time=ts,
        entry_price=slot.entry_price,
        exit_price=exit_price,
        stop_loss=slot.stop_loss or 0,
        take_profit=slot.take_profit or 0,
        atr_at_entry=slot.entry_atr or 0,
        size=close_size,
        pnl=pnl,
        pnl_gross=gross,
        commission=comm,
        reason=reason,
        bars_held=bars_held,
    ))


# ────────────────────────────────────────────────────────────────
#  Vectorised pre-computation
# ────────────────────────────────────────────────────────────────

# Runtime feature precompute moved to strategy.runtime so bot_manager
# (live trading) uses the same logic. Imported above as
# `precompute_runtime_features`.
