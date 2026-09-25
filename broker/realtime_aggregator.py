"""
Aggregates real-time Time & Sales and DOM data into per-bar metrics.

Time & Sales → per-bar buy/sell volume delta (footprint-style)
DOM          → bid/ask size imbalance ratio

These values feed directly into the confluence scoring signals
that were previously disabled due to no footprint data.
"""

import threading
import logging
from datetime import datetime, timezone
import config as cfg

logger = logging.getLogger(__name__)


class RealtimeAggregator:
    """
    Thread-safe aggregator for T&S and DOM data.
    Call reset_bar() at the start of each new 1-minute bar.
    """

    def __init__(self):
        self._lock = threading.Lock()

        # ── Per-bar T&S accumulators ──
        self._buy_volume  = 0.0
        self._sell_volume = 0.0
        self._trade_count = 0

        # ── Latest DOM snapshot ──
        self._bids = []   # list of {price, size}
        self._asks = []   # list of {price, size}

        # ── Last trade price (for T&S direction) ──
        self._last_price = None

    # ──────────────────────────────────────────
    #  Callbacks (connected to MarketDataStream)
    # ──────────────────────────────────────────

    def on_trade(self, payload):
        """
        Handle GatewayTrade event.
        payload may be a single trade dict or a list of trade dicts.
        Trade dict expected keys: price, volume/size, side (0=buy,1=sell)
        or we infer direction from price vs last price.
        """
        if isinstance(payload, list):
            for t in payload:
                self._process_trade(t)
        elif isinstance(payload, dict):
            self._process_trade(payload)

    def on_depth(self, payload):
        """
        Handle GatewayDepth event.
        payload is typically a list of level dicts with price and size,
        or a dict with bids/asks keys.
        """
        try:
            with self._lock:
                if isinstance(payload, dict):
                    self._bids = payload.get("bids", [])
                    self._asks = payload.get("asks", [])
                elif isinstance(payload, list):
                    # Some APIs send a flat list — split by side field
                    self._bids = [l for l in payload if l.get("side", 0) == 0]
                    self._asks = [l for l in payload if l.get("side", 1) == 1]
        except Exception as e:
            logger.debug(f"DOM parse error: {e}")

    # ──────────────────────────────────────────
    #  Bar lifecycle
    # ──────────────────────────────────────────

    def reset_bar(self):
        """Call at the start of each new 1-minute bar to clear T&S accumulators."""
        with self._lock:
            self._buy_volume  = 0.0
            self._sell_volume = 0.0
            self._trade_count = 0

    # ──────────────────────────────────────────
    #  Derived metrics (read by strategy)
    # ──────────────────────────────────────────

    def get_bar_delta(self) -> float:
        """Buy volume minus sell volume for the current bar."""
        with self._lock:
            return self._buy_volume - self._sell_volume

    def get_bar_buy_volume(self) -> float:
        with self._lock:
            return self._buy_volume

    def get_bar_sell_volume(self) -> float:
        with self._lock:
            return self._sell_volume

    def get_bar_total_volume(self) -> float:
        with self._lock:
            return self._buy_volume + self._sell_volume

    def get_delta_pct(self) -> float:
        """Delta as a percentage of total volume (0–100)."""
        with self._lock:
            total = self._buy_volume + self._sell_volume
            if total == 0:
                return 0.0
            return ((self._buy_volume - self._sell_volume) / total) * 100

    def is_bullish_delta(self) -> bool:
        """True if buy volume exceeds sell volume by more than threshold."""
        return self.get_bar_delta() > getattr(cfg, "TS_DELTA_THRESH", 0.0)

    def is_bearish_delta(self) -> bool:
        """True if sell volume exceeds buy volume by more than threshold."""
        return self.get_bar_delta() < -getattr(cfg, "TS_DELTA_THRESH", 0.0)

    def get_dom_imbalance(self) -> float:
        """
        Bid/ask size imbalance ratio.
        > 0  → more bids (buying pressure)
        < 0  → more asks (selling pressure)
        Range: roughly -1.0 to +1.0
        """
        with self._lock:
            bid_size = sum(l.get("size", l.get("volume", 0)) for l in self._bids[:5])
            ask_size = sum(l.get("size", l.get("volume", 0)) for l in self._asks[:5])
            total = bid_size + ask_size
            if total == 0:
                return 0.0
            return (bid_size - ask_size) / total

    def is_dom_bullish(self) -> bool:
        """True when top-5 bid levels outweigh top-5 ask levels."""
        return self.get_dom_imbalance() > getattr(cfg, "DOM_IMBALANCE_THRESH", 0.0)

    def is_dom_bearish(self) -> bool:
        """True when top-5 ask levels outweigh top-5 bid levels."""
        return self.get_dom_imbalance() < -getattr(cfg, "DOM_IMBALANCE_THRESH", 0.0)

    def get_snapshot(self) -> dict:
        """Return all current metrics as a dict for logging/strategy use."""
        return {
            "buy_vol":      self.get_bar_buy_volume(),
            "sell_vol":     self.get_bar_sell_volume(),
            "delta":        self.get_bar_delta(),
            "delta_pct":    self.get_delta_pct(),
            "dom_imbalance": self.get_dom_imbalance(),
            "bullish_delta": self.is_bullish_delta(),
            "bearish_delta": self.is_bearish_delta(),
            "dom_bullish":  self.is_dom_bullish(),
            "dom_bearish":  self.is_dom_bearish(),
        }

    # ──────────────────────────────────────────
    #  Internal
    # ──────────────────────────────────────────

    def _process_trade(self, trade: dict):
        try:
            price  = float(trade.get("price", 0))
            volume = float(trade.get("volume") or trade.get("size") or trade.get("quantity") or 0)
            side   = trade.get("side")   # 0=buy, 1=sell, or None

            with self._lock:
                if side == 0:
                    self._buy_volume += volume
                elif side == 1:
                    self._sell_volume += volume
                else:
                    # Infer direction from price vs last price (uptick/downtick)
                    if self._last_price is not None:
                        if price >= self._last_price:
                            self._buy_volume += volume
                        else:
                            self._sell_volume += volume
                    else:
                        # Can't determine direction on first trade
                        self._buy_volume += volume / 2
                        self._sell_volume += volume / 2

                self._last_price = price
                self._trade_count += 1

        except Exception as e:
            logger.debug(f"Trade processing error: {e}  trade={trade}")
