"""
Base classes for the pluggable strategy system (Phase 1 architecture).

Concepts
--------
- Strategy: stateless evaluator. Given a bar + slot state, returns
  EntrySignal / ExitSignal / trail updates. Owns no position state.
- StrategySlot: per (strategy, instrument) position state. Holds the
  trade direction, entry/SL/TP, broker order ids, and cooldown counter.
- bot_manager owns a list of slots per instrument and is the only
  component that talks to the broker.

Each strategy is independent: a single instrument can have multiple
slots open in opposite directions (broker-level positions are netted,
but each slot's own bracket orders close only its own contract(s)).
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EntrySignal:
    direction: int          # +1 long, -1 short
    label: str              # human-readable reason e.g. "BreakoutLong"
    stop_loss: float
    take_profit: float
    trail_stop: float
    atr: float
    reference_price: float = 0.0   # bar close used if broker omits fill price
    size: int = 1


@dataclass
class ExitSignal:
    should_exit: bool
    reason: str = ""
    size: int = 0       # 0 = close all remaining; >0 = close this many contracts
    exit_price: float = 0.0  # 0 = market exit at close; >0 = limit fill at this price


@dataclass
class StrategySlot:
    """Per (strategy, instrument) logical position state."""
    strategy_name: str
    symbol: str

    # Position
    trade_dir: int = 0
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    trail_stop: Optional[float] = None
    entry_atr: Optional[float] = None
    size: int = 1

    # Broker order ids for THIS slot's bracket orders
    sl_order_id: Optional[int] = None
    tp_order_id: Optional[int] = None

    # Cooldown / freshness
    bars_since_exit: int = 100

    # Strategy-private scratchpad (free-form per-strategy state)
    extra: dict = field(default_factory=dict)

    @property
    def flat(self) -> bool:
        return self.trade_dir == 0

    def reset(self):
        self.trade_dir = 0
        self.entry_price = None
        self.stop_loss = None
        self.take_profit = None
        self.trail_stop = None
        self.entry_atr = None
        self.sl_order_id = None
        self.tp_order_id = None
        self.bars_since_exit = 0


class Strategy(ABC):
    """Abstract base class for a trading strategy.

    Subclasses implement evaluate_entry / evaluate_exit / update_trail.
    Strategies are stateless w.r.t. position — all per-position state
    lives on the StrategySlot passed in.
    """

    name: str = "BaseStrategy"
    # If True, the backtest engine will populate ctx["df"] with a trailing
    # window DataFrame (expensive — O(n²) memory churn). Leave False unless
    # a strategy genuinely needs the raw DataFrame slice.
    needs_df_window: bool = False

    def __init__(self, name: Optional[str] = None):
        if name:
            self.name = name

    def on_bar_start(self, slot: StrategySlot, row: dict, ctx: dict) -> None:
        """Called once per bar before evaluate_* — bookkeeping hook."""
        pass

    @abstractmethod
    def evaluate_entry(self, slot: StrategySlot, row: dict, ctx: dict) -> Optional[EntrySignal]:
        """Return an EntrySignal if a new entry should fire, else None.
        Only called when slot.flat is True."""
        ...

    @abstractmethod
    def evaluate_exit(self, slot: StrategySlot, row: dict, ctx: dict) -> Optional[ExitSignal]:
        """Return an ExitSignal if the open position should close, else None.
        Only called when slot.flat is False."""
        ...

    def update_trail(self, slot: StrategySlot, row: dict, ctx: dict) -> Optional[float]:
        """Optional. Return new SL price if the trailing stop should move,
        else None. Only called when slot.flat is False."""
        return None
