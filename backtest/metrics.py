"""
Performance metrics for backtest results.

Computes institutional-standard metrics from a list of Trade objects:
  - Total P&L, trade count, win rate
  - Profit factor, expectancy
  - Sharpe ratio, Sortino ratio
  - Max drawdown ($ and %)
  - Average win / loss, largest win / loss
  - Average bars held
  - Longest winning / losing streak
"""

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class Metrics:
    total_pnl: float
    total_trades: int
    winners: int
    losers: int
    win_rate: float
    profit_factor: float
    expectancy: float
    sharpe: float
    sortino: float
    max_drawdown: float
    max_drawdown_pct: float
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    avg_bars_held: float
    longest_win_streak: int
    longest_lose_streak: int
    total_commission: float
    gross_pnl: float

    def summary(self, symbol: str = "", strategy: str = "") -> str:
        header = f"{strategy} on {symbol}" if symbol else strategy
        return (
            f"\n{'=' * 60}\n"
            f"  {header}\n"
            f"{'=' * 60}\n"
            f"  Period trades  : {self.total_trades}\n"
            f"  Winners/Losers : {self.winners} / {self.losers}\n"
            f"  Win rate       : {self.win_rate:.1f}%\n"
            f"  Profit factor  : {self.profit_factor:.2f}\n"
            f"  Expectancy     : ${self.expectancy:.2f} / trade\n"
            f"{'─' * 60}\n"
            f"  Total PnL      : ${self.total_pnl:+,.2f}  "
            f"(gross ${self.gross_pnl:+,.2f}  comm ${self.total_commission:,.2f})\n"
            f"  Max drawdown   : ${self.max_drawdown:,.2f}  "
            f"({self.max_drawdown_pct:.1f}%)\n"
            f"{'─' * 60}\n"
            f"  Sharpe ratio   : {self.sharpe:.2f}\n"
            f"  Sortino ratio  : {self.sortino:.2f}\n"
            f"{'─' * 60}\n"
            f"  Avg win        : ${self.avg_win:+.2f}\n"
            f"  Avg loss       : ${self.avg_loss:+.2f}\n"
            f"  Largest win    : ${self.largest_win:+.2f}\n"
            f"  Largest loss   : ${self.largest_loss:+.2f}\n"
            f"  Avg bars held  : {self.avg_bars_held:.1f}\n"
            f"  Win streak     : {self.longest_win_streak}\n"
            f"  Lose streak    : {self.longest_lose_streak}\n"
            f"{'=' * 60}\n"
        )


def compute_metrics(trades: list, initial_equity: float = 50_000.0) -> Metrics:
    """Compute full performance metrics from a list of Trade objects."""
    if not trades:
        return Metrics(
            total_pnl=0, total_trades=0, winners=0, losers=0,
            win_rate=0, profit_factor=0, expectancy=0,
            sharpe=0, sortino=0, max_drawdown=0, max_drawdown_pct=0,
            avg_win=0, avg_loss=0, largest_win=0, largest_loss=0,
            avg_bars_held=0, longest_win_streak=0, longest_lose_streak=0,
            total_commission=0, gross_pnl=0,
        )

    pnls = np.array([t.pnl for t in trades])
    gross_pnls = np.array([t.pnl_gross for t in trades])
    commissions = np.array([t.commission for t in trades])
    bars_held = np.array([t.bars_held for t in trades])

    total_pnl = float(pnls.sum())
    gross_pnl = float(gross_pnls.sum())
    total_commission = float(commissions.sum())
    n = len(trades)

    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    n_win = len(wins)
    n_loss = len(losses)
    win_rate = (n_win / n * 100) if n > 0 else 0.0

    total_win = float(wins.sum()) if len(wins) > 0 else 0.0
    total_loss = float(abs(losses.sum())) if len(losses) > 0 else 0.0
    profit_factor = (total_win / total_loss) if total_loss > 0 else float("inf")

    expectancy = total_pnl / n if n > 0 else 0.0

    # Sharpe and Sortino (annualised, assuming ~252 trading days, ~10 trades/day avg)
    if len(pnls) > 1:
        mean_pnl = pnls.mean()
        std_pnl = pnls.std(ddof=1)
        sharpe = (mean_pnl / std_pnl * math.sqrt(252 * 10)) if std_pnl > 0 else 0.0
        downside = pnls[pnls < 0]
        if len(downside) > 1:
            downside_std = downside.std(ddof=1)
            sortino = (mean_pnl / downside_std * math.sqrt(252 * 10)) if downside_std > 0 else 0.0
        else:
            sortino = 0.0
    else:
        sharpe = 0.0
        sortino = 0.0

    # Max drawdown
    equity_curve = np.cumsum(pnls) + initial_equity
    peak = np.maximum.accumulate(equity_curve)
    drawdown = peak - equity_curve
    max_dd = float(drawdown.max())
    # Max DD as percentage of peak at that point
    dd_pct_arr = drawdown / peak * 100
    max_dd_pct = float(dd_pct_arr.max()) if len(dd_pct_arr) > 0 else 0.0

    avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
    avg_loss = float(losses.mean()) if len(losses) > 0 else 0.0
    largest_win = float(wins.max()) if len(wins) > 0 else 0.0
    largest_loss = float(losses.min()) if len(losses) > 0 else 0.0

    avg_bars = float(bars_held.mean()) if len(bars_held) > 0 else 0.0

    # Streaks
    win_streak = 0
    lose_streak = 0
    max_win_streak = 0
    max_lose_streak = 0
    for p in pnls:
        if p > 0:
            win_streak += 1
            lose_streak = 0
        else:
            lose_streak += 1
            win_streak = 0
        max_win_streak = max(max_win_streak, win_streak)
        max_lose_streak = max(max_lose_streak, lose_streak)

    return Metrics(
        total_pnl=total_pnl,
        total_trades=n,
        winners=n_win,
        losers=n_loss,
        win_rate=win_rate,
        profit_factor=profit_factor,
        expectancy=expectancy,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        avg_win=avg_win,
        avg_loss=avg_loss,
        largest_win=largest_win,
        largest_loss=largest_loss,
        avg_bars_held=avg_bars,
        longest_win_streak=max_win_streak,
        longest_lose_streak=max_lose_streak,
        total_commission=total_commission,
        gross_pnl=gross_pnl,
    )
