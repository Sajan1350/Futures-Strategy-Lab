"""
Risk management: Daily loss kill switch and consecutive loss kill switches.
Mirrors the PineScript kill switch logic exactly.
"""

import logging
from datetime import date
import config as cfg

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, initial_equity: float = 50000.0):
        self.initial_equity  = initial_equity
        self.current_equity  = initial_equity

        # Daily loss tracking
        self._daily_pnl      = 0.0
        self._last_trade_day = None
        self._kill_triggered = False

        # Consecutive loss tracking
        self._consec_losses      = 0
        self._consec_loss_total  = 0.0
        self._consec_pause_until = None   # datetime when pause ends
        self._consec_pause_level = 0      # 0=none, 3=3-loss, 5=5-loss

    # ──────────────────────────────────────────
    #  Public API
    # ──────────────────────────────────────────

    def new_day(self):
        """Call at the start of each new trading day."""
        today = date.today()
        if self._last_trade_day != today:
            logger.info(f"New day {today} – resetting daily kill switch.")
            self._daily_pnl      = 0.0
            self._kill_triggered = False
            self._consec_losses      = 0
            self._consec_loss_total  = 0.0
            self._consec_pause_until = None
            self._consec_pause_level = 0
            self._last_trade_day = today

    def record_trade(self, pnl: float):
        """Record a closed trade P&L and update kill switch state."""
        self._daily_pnl    += pnl
        self.current_equity += pnl

        # Daily kill switch
        threshold = self._kill_threshold()
        if cfg.USE_KILL_SWITCH and self._daily_pnl <= -threshold:
            if not self._kill_triggered:
                logger.warning(
                    f"Daily kill switch triggered! Day P&L=${self._daily_pnl:.2f} "
                    f"(threshold=${-threshold:.2f})"
                )
            self._kill_triggered = True

        # Consecutive loss tracking
        if pnl < 0:
            self._consec_losses     += 1
            self._consec_loss_total += abs(pnl)
        else:
            self._consec_losses      = 0
            self._consec_loss_total  = 0.0
            self._consec_pause_level = 0

        # Apply consecutive-loss pauses
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)

        if cfg.USE_CONSEC5_KILL and self._consec_losses >= 5 and self._consec_pause_level < 5:
            ratio5 = min(self._consec_loss_total / cfg.CONSEC5_MAX_LOSS, 1.0)
            pause_mins = cfg.CONSEC5_MIN_PAUSE + ratio5 * (cfg.CONSEC5_MAX_PAUSE - cfg.CONSEC5_MIN_PAUSE)
            self._consec_pause_until = now + timedelta(minutes=pause_mins)
            self._consec_pause_level = 5
            logger.warning(
                f"5-loss streak! Pausing for {pause_mins:.0f} minutes "
                f"(until {self._consec_pause_until.strftime('%H:%M UTC')})."
            )

        elif cfg.USE_CONSEC3_KILL and self._consec_losses >= 3 and self._consec_pause_level < 3:
            ratio3 = min(self._consec_loss_total / cfg.CONSEC3_MAX_LOSS, 1.0)
            pause_mins = cfg.CONSEC3_MIN_PAUSE + ratio3 * (cfg.CONSEC3_MAX_PAUSE - cfg.CONSEC3_MIN_PAUSE)
            self._consec_pause_until = now + timedelta(minutes=pause_mins)
            self._consec_pause_level = 3
            logger.warning(
                f"3-loss streak! Pausing for {pause_mins:.0f} minutes "
                f"(until {self._consec_pause_until.strftime('%H:%M UTC')})."
            )

    @property
    def kill_triggered(self) -> bool:
        return self._kill_triggered

    @property
    def consec_kill_active(self) -> bool:
        if self._consec_pause_until is None:
            return False
        from datetime import datetime, timezone
        return datetime.now(timezone.utc) < self._consec_pause_until

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def consec_losses(self) -> int:
        return self._consec_losses

    @property
    def pause_level(self) -> int:
        return self._consec_pause_level if self.consec_kill_active else 0

    def status_str(self) -> str:
        lines = [
            f"  Daily P&L   : ${self._daily_pnl:+.2f}",
            f"  Kill switch : {'TRIGGERED' if self._kill_triggered else 'Active'}",
            f"  Consec losses: {self._consec_losses}  (${self._consec_loss_total:.2f})",
        ]
        if self.consec_kill_active:
            from datetime import datetime, timezone
            mins_left = (self._consec_pause_until - datetime.now(timezone.utc)).total_seconds() / 60
            lines.append(f"  Consec pause : {self._consec_pause_level}-loss, {mins_left:.0f}m left")
        return "\n".join(lines)

    # ──────────────────────────────────────────
    #  Internal
    # ──────────────────────────────────────────

    def _kill_threshold(self) -> float:
        if cfg.KILL_MODE == "Dollar":
            return cfg.KILL_DOLLAR
        return (cfg.KILL_PERCENT / 100.0) * self.current_equity
