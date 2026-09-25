"""
Strategy template — a wiring reference, NOT a strategy.

The logic below is placeholder: a 9/20 EMA cross with a fixed ATR stop and
a single Keltner target. It has no researched edge and is not intended to
be traded or tuned. It exists so that:

  1. there is a complete, working example of every hook on the `Strategy`
     contract, with the calling conventions spelled out; and
  2. `python -m backtest.run --strategy Template --symbol MGC` exercises
     the whole pipeline end to end, which is how you confirm the platform
     works before trusting a result from a real strategy.

To start a real strategy, copy this file, rename the class and its `name`,
and replace the body of every hook. Keep the strategy's own parameters in
the strategy module (as below) rather than in config.py — config.py is for
shared machinery only, so a strategy stays readable and deletable in one
piece.

Columns available on `row` (all floats unless noted):

  price      open, high, low, close, volume, timestamp
  trend      ema_9, ema_20, ema_50_1h
  vol        atr_fast, atr_slow
  keltner    kc_basis, kc_upper1/2, kc_lower1/2, kc_upper_tp, kc_lower_tp,
             kc_bw, kc_bw_ma, kc_squeeze (bool), kc_slope_flat (bool)
  regime     is_range (bool)
  volume     volume_ma
  levels     vwap, yest_high, yest_low, yest_close
  candle     body_top, body_bot, body_mid
  session    hour_pt (int), in_session (bool), is_first_half (bool),
             session_date, bars_since_bull_cross, bars_since_bear_cross
  optional   bias_direction (only when the engine is asked for it)

Run `python -m core.indicator_catalog` for the full catalog with the
config knob behind each one.

Keys on `ctx`:

  symbol, inst_cfg      instrument definition (tick size, point value, ...)
  in_session            bar is inside the instrument's session window
  range_blocked         the range-regime detector fired on this bar
  bias_direction        +1 / -1 / 0 (0 unless locked bias was enabled)
  can_enter             False when a kill switch or the daily loss limit
                        is active — always respect it before entering
  risk_mgr              the live RiskManager
  entry_pnl_history     per-strategy deque of the last 4 completed
                        entry-level PnLs, for result-dependent sizing
"""

from __future__ import annotations

import math
from typing import Optional

from strategies import register
from strategies.base import Strategy, EntrySignal, ExitSignal, StrategySlot

# ── Strategy-local parameters ────────────────────────────────────
# Placeholder values. A real strategy keeps its parameters here, where
# they can be read alongside the rules that consume them.
SL_ATR_MULT     = 1.5    # stop distance = SL_ATR_MULT x ATR(slow)
CROSS_MAX_AGE   = 15     # only enter within N bars of the EMA cross
SIZE            = 2      # contracts per entry
SCALE_OUT       = True   # take half at KC1, run the rest to KC2


@register
class TemplateStrategy(Strategy):
    """PLACEHOLDER logic — see module docstring. Do not trade this."""

    name = "Template"

    # ── Entry ────────────────────────────────────────────────────
    def evaluate_entry(self, slot: StrategySlot, row: dict,
                       ctx: dict) -> Optional[EntrySignal]:
        # The engine only calls this when the slot is flat.

        # Risk gates first — cheapest checks, and can_enter is mandatory.
        if not ctx["can_enter"] or not ctx["in_session"]:
            return None
        if ctx["range_blocked"]:
            return None

        atr = row["atr_slow"]
        if not atr or atr != atr:          # NaN guard during warmup
            return None

        close = row["close"]

        # --- placeholder rule: fresh 9/20 cross, confirmed by the 1H EMA ---
        if row["ema_9"] > row["ema_20"] and row["bars_since_bull_cross"] <= CROSS_MAX_AGE \
                and close > row["ema_50_1h"]:
            direction = 1
        elif row["ema_9"] < row["ema_20"] and row["bars_since_bear_cross"] <= CROSS_MAX_AGE \
                and close < row["ema_50_1h"]:
            direction = -1
        else:
            return None

        stop = close - direction * SL_ATR_MULT * atr
        target = row["kc_upper2"] if direction == 1 else row["kc_lower2"]

        # Reset any per-trade scratch state from the previous position.
        slot.extra.clear()

        return EntrySignal(
            direction=direction,
            label=f"{self.name}{'Long' if direction == 1 else 'Short'}",
            stop_loss=stop,
            take_profit=target,
            trail_stop=stop,
            atr=atr,
            reference_price=close,
            size=SIZE,
        )

    # ── Exit ─────────────────────────────────────────────────────
    def evaluate_exit(self, slot: StrategySlot, row: dict,
                      ctx: dict) -> Optional[ExitSignal]:
        # Called only while the slot holds a position. The engine checks
        # the stop itself before calling this, so handle targets here.
        d = slot.trade_dir
        band1 = row["kc_upper1"] if d == 1 else row["kc_lower1"]
        band2 = row["kc_upper2"] if d == 1 else row["kc_lower2"]

        touched_1 = row["high"] >= band1 if d == 1 else row["low"] <= band1
        touched_2 = row["high"] >= band2 if d == 1 else row["low"] <= band2

        if SCALE_OUT and not slot.extra.get("scaled") and touched_1 and slot.size > 1:
            # Partial exit: close half at the band price, keep the rest.
            # Returning size < slot.size tells the engine to reduce, not close.
            slot.extra["scaled"] = True
            slot.extra["stop_label"] = "BreakevenStop"   # names a later stop-out
            slot.stop_loss = slot.entry_price            # move stop to breakeven
            return ExitSignal(
                should_exit=True,
                reason="Scale1",
                size=math.ceil(slot.size / 2),
                exit_price=band1,     # limit-style fill AT the band
            )

        if touched_2:
            # size=0 means close whatever remains.
            return ExitSignal(should_exit=True, reason="Target",
                              size=0, exit_price=band2)

        return None

    # ── Trailing stop ────────────────────────────────────────────
    def update_trail(self, slot: StrategySlot, row: dict,
                     ctx: dict) -> Optional[float]:
        # Return a new stop price, or None to leave it. Only ratchet in the
        # favourable direction — a stop that can loosen is not a stop.
        if not slot.extra.get("scaled"):
            return None

        d = slot.trade_dir
        candidate = row["kc_upper1"] if d == -1 else row["kc_lower1"]
        if d == 1 and candidate > slot.stop_loss:
            slot.extra["stop_label"] = "TrailStop"
            return candidate
        if d == -1 and candidate < slot.stop_loss:
            slot.extra["stop_label"] = "TrailStop"
            return candidate
        return None
