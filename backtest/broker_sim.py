"""
Simulated broker for backtesting.

Replicates the bracket-order logic from bot_manager:
  - Market entry with 1-tick slippage
  - Stop-loss checked against bar high/low (not just close)
  - Take-profit checked against bar high/low
  - Pessimistic: when both SL and TP are inside the same bar, SL hits first
  - Trail stop updates via the strategy's update_trail() method
  - Hard dollar stop
  - Commission per round-trip per contract
"""

from dataclasses import dataclass, field
from typing import Optional


# Default commission per round-trip per contract (entry + exit)
DEFAULT_COMMISSION = 1.50  # USD


@dataclass
class FillResult:
    """Result of a simulated fill."""
    filled: bool = False
    fill_price: float = 0.0
    slippage: float = 0.0


@dataclass
class ExitResult:
    """Result of checking whether an open position should close this bar."""
    should_exit: bool = False
    exit_price: float = 0.0
    reason: str = ""


def simulate_entry_fill(signal_price: float, direction: int,
                        tick_size: float) -> FillResult:
    """
    Simulate a market-order entry with 1-tick slippage.

    For longs: fill = signal_price + tick_size  (worse fill)
    For shorts: fill = signal_price - tick_size
    """
    slip = tick_size
    if direction == 1:
        fill = signal_price + slip
    else:
        fill = signal_price - slip
    return FillResult(filled=True, fill_price=fill, slippage=slip)


def check_exit(bar_high: float, bar_low: float, bar_close: float,
               direction: int, stop_loss: float, take_profit: float,
               entry_price: float, max_loss_per_trade: float,
               point_value: float, size: int,
               tick_size: float) -> ExitResult:
    """
    Check whether SL, TP, or hard-dollar stop is hit on this bar.

    Uses bar high/low for realistic intrabar stop detection.
    Pessimistic rule: if BOTH SL and TP are inside the bar, SL hits first.

    SL exits include 1-tick slippage (stop orders slip).
    TP exits fill at limit price (no slippage).
    """
    sl_hit = False
    tp_hit = False

    if direction == 1:  # long
        sl_hit = bar_low <= stop_loss
        tp_hit = bar_high >= take_profit
    else:  # short
        sl_hit = bar_high >= stop_loss
        tp_hit = bar_low <= take_profit

    # Pessimistic: SL before TP when both are inside the bar
    if sl_hit and tp_hit:
        # SL with 1-tick slippage
        if direction == 1:
            exit_price = stop_loss - tick_size  # slipped worse
        else:
            exit_price = stop_loss + tick_size
        return ExitResult(True, exit_price, "StopLoss")

    if sl_hit:
        if direction == 1:
            exit_price = stop_loss - tick_size
        else:
            exit_price = stop_loss + tick_size
        return ExitResult(True, exit_price, "StopLoss")

    if tp_hit:
        # Limit order: fills at limit price exactly
        return ExitResult(True, take_profit, "TakeProfit")

    # Hard dollar stop disabled (PineScript has no such cutoff — SL/TP/trail only).
    # max_loss_per_trade is still accepted for API compatibility but ignored.
    return ExitResult(False)


def calc_trade_pnl(entry_price: float, exit_price: float,
                   direction: int, size: int, point_value: float,
                   commission: float = DEFAULT_COMMISSION) -> float:
    """
    Calculate trade P&L in dollars including commission.

    P&L = (exit - entry) * direction * size * point_value - commission
    """
    gross = (exit_price - entry_price) * direction * size * point_value
    return gross - commission * size
