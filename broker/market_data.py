"""
Real-time market data via SignalR (ProjectX market hub).

Key fixes vs original:
- Token passed as URL query param (?access_token=...), NOT in headers
- skip_negotiation: True for direct WebSocket (no HTTP negotiate step)
- Subscribes to Quotes, Trades (T&S), and MarketDepth (DOM)
"""

import os
import logging
import threading
from signalrcore.hub_connection_builder import HubConnectionBuilder

logger = logging.getLogger(__name__)


def _build_market_hub(token: str) -> object:
    base = os.getenv("PROJECTX_HUB_URL", "https://rtc.topstepx.com")
    # Convert http(s) → ws(s) and append token as query param
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    url = f"{ws_base}/hubs/market?access_token={token}"

    return (
        HubConnectionBuilder()
        .with_url(url, options={"skip_negotiation": True, "verify_ssl": True})
        .with_automatic_reconnect({
            "type": "raw",
            "keep_alive_interval": 10,
            "reconnect_interval": 5,
        })
        .build()
    )


def _build_user_hub(token: str) -> object:
    base = os.getenv("PROJECTX_HUB_URL", "https://rtc.topstepx.com")
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    url = f"{ws_base}/hubs/user?access_token={token}"

    return (
        HubConnectionBuilder()
        .with_url(url, options={"skip_negotiation": True, "verify_ssl": True})
        .with_automatic_reconnect({
            "type": "raw",
            "keep_alive_interval": 10,
            "reconnect_interval": 5,
        })
        .build()
    )


class MarketDataStream:
    """
    Streams real-time Quotes, Time & Sales, and DOM for a contract.

    Callbacks you can register:
        on_quote   : fn(quote_dict)
        on_trade   : fn(trade_dict_or_list)
        on_depth   : fn(depth_list)
    """

    def __init__(self, token: str, contract_id: str):
        self.token       = token
        self.contract_id = contract_id
        self._hub        = None
        self._connected  = threading.Event()

        self.last_quote  = {}
        self.on_quote    = None
        self.on_trade    = None
        self.on_depth    = None

    def start(self):
        self._hub = _build_market_hub(self.token)
        self._hub.on_open(self._on_open)
        self._hub.on_close(lambda: logger.warning("Market hub disconnected."))
        self._hub.on_error(lambda e: logger.debug(f"Market hub error: {e}"))
        self._hub.on("GatewayQuote", self._handle_quote)
        self._hub.on("GatewayTrade", self._handle_trade)
        self._hub.on("GatewayDepth", self._handle_depth)
        self._hub.start()
        if not self._connected.wait(timeout=15):
            logger.warning("Market hub: no connection confirmation within 15s — continuing anyway.")

    def stop(self):
        if self._hub:
            try:
                self._hub.stop()
            except Exception:
                pass

    def update_token(self, new_token: str):
        """Call periodically to refresh the token for long-running connections."""
        self.token = new_token
        self.stop()
        self.start()

    # ── Internal handlers ──

    def _on_open(self):
        logger.info("Market hub connected — subscribing to quotes, trades, DOM.")
        self._connected.set()
        cid = self.contract_id
        self._hub.send("SubscribeContractQuotes",       [cid])
        self._hub.send("SubscribeContractTrades",       [cid])
        self._hub.send("SubscribeContractMarketDepth",  [cid])

    def _handle_quote(self, data):
        try:
            payload = data[1] if isinstance(data, list) and len(data) > 1 else (data[0] if data else {})
            self.last_quote = payload
            if self.on_quote:
                self.on_quote(payload)
        except Exception as e:
            logger.debug(f"Quote parse error: {e}")

    def _handle_trade(self, data):
        try:
            payload = data[1] if isinstance(data, list) and len(data) > 1 else (data[0] if data else {})
            if self.on_trade:
                self.on_trade(payload)
        except Exception as e:
            logger.debug(f"Trade parse error: {e}")

    def _handle_depth(self, data):
        try:
            payload = data[1] if isinstance(data, list) and len(data) > 1 else (data[0] if data else [])
            if self.on_depth:
                self.on_depth(payload)
        except Exception as e:
            logger.debug(f"Depth parse error: {e}")


class UserStream:
    """Streams order/position updates from the user hub."""

    def __init__(self, token: str):
        self.token           = token
        self._hub            = None
        self._connected      = threading.Event()
        self.on_order_update = None

    def start(self):
        self._hub = _build_user_hub(self.token)
        self._hub.on_open(self._on_open)
        self._hub.on_close(lambda: logger.warning("User hub disconnected."))
        self._hub.on_error(lambda e: logger.debug(f"User hub error: {e}"))
        self._hub.on("GatewayUserOrder", self._handle_order)
        self._hub.start()
        if not self._connected.wait(timeout=15):
            logger.warning("User hub: no connection confirmation within 15s — continuing anyway.")

    def stop(self):
        if self._hub:
            try:
                self._hub.stop()
            except Exception:
                pass

    def _on_open(self):
        logger.info("User hub connected.")
        self._connected.set()

    def _handle_order(self, data):
        try:
            order = data[1] if isinstance(data, list) and len(data) > 1 else (data[0] if data else {})
            logger.info(f"Order update: {order}")
            if self.on_order_update:
                self.on_order_update(order)
        except Exception as e:
            logger.debug(f"Order update parse error: {e}")
