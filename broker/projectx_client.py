"""
ProjectX / TopStepX REST API client.
Handles authentication, contract search, account info,
historical data, and order management.
"""

import os
import logging
import requests
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Bar unit constants for History API
UNIT_MINUTE = 2
UNIT_HOUR   = 3
UNIT_DAY    = 4

# Default tick sizes per instrument
TICK_SIZES = {
    "MGC": 0.10,
    "MNQ": 0.25,
    "MES": 0.25,
    "MCL": 0.01,
}
DEFAULT_TICK = 0.10

def _round_tick(price: float, tick: float = DEFAULT_TICK) -> float:
    """Round a price to the nearest valid tick increment."""
    return round(round(price / tick) * tick, 10)


class ProjectXClient:
    def __init__(self):
        self.base_url = os.getenv("PROJECTX_BASE_URL", "https://api.topstepx.com/api")
        self.username = os.getenv("PROJECTX_USERNAME")
        self.api_key  = os.getenv("PROJECTX_API_KEY")
        self.token    = None
        self.token_expiry = None
        self._session = requests.Session()
        self.live     = True   # set to False for evaluation/sim accounts

    # ──────────────────────────────────────────
    #  Authentication
    # ──────────────────────────────────────────

    def login(self):
        """Authenticate and store JWT token."""
        url  = f"{self.base_url}/Auth/loginKey"
        body = {"userName": self.username, "apiKey": self.api_key}
        resp = self._session.post(url, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        self.token = data.get("token")
        if not self.token:
            raise ValueError(f"Login failed – no token in response: {data}")
        # Tokens typically expire in 24 h; refresh 1 h before
        self.token_expiry = datetime.now(timezone.utc) + timedelta(hours=23)
        self._session.headers.update({"Authorization": f"Bearer {self.token}"})
        logger.info("Authenticated with ProjectX successfully.")

    def _ensure_token(self):
        if self.token is None or datetime.now(timezone.utc) >= self.token_expiry:
            logger.info("Token missing or expired – re-authenticating.")
            self.login()

    def _get(self, path, params=None):
        self._ensure_token()
        resp = self._session.get(f"{self.base_url}{path}", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path, body):
        self._ensure_token()
        resp = self._session.post(f"{self.base_url}{path}", json=body, timeout=10)
        if not resp.ok:
            try:
                err_body = resp.json()
            except Exception:
                err_body = resp.text
            logger.error(f"POST {path} {resp.status_code}: {err_body}  (sent: {body})")
        resp.raise_for_status()
        return resp.json()

    # ──────────────────────────────────────────
    #  Account
    # ──────────────────────────────────────────

    def get_accounts(self):
        """Return list of tradeable accounts."""
        data = self._post("/Account/search", {"onlyActiveAccounts": True})
        return data.get("accounts", [])

    def get_positions(self, account_id):
        """Return open positions for an account."""
        data = self._post("/Position/searchOpen", {"accountId": account_id})
        return data.get("positions", [])

    def get_open_orders(self, account_id):
        """Return open orders for an account."""
        data = self._post("/Order/searchOpen", {"accountId": account_id})
        return data.get("orders", [])

    # ──────────────────────────────────────────
    #  Contract Search
    # ──────────────────────────────────────────

    def search_contracts(self, text, live=True):
        """Search for contracts by symbol text. Returns list of contracts."""
        data = self._post("/Contract/search", {"searchText": text, "live": live})
        logger.debug(f"Contract search response: {data}")
        return data.get("contracts", [])

    def get_front_month_contract(self, symbol):
        """
        Find the front-month contract for a symbol (e.g. 'MGC').
        Tries live=True first, then live=False (for evaluation accounts).
        """
        # Try live=True first, then live=False (evaluation accounts use live=False)
        for live in (True, False):
            contracts = self.search_contracts(symbol, live=live)
            if contracts:
                self.live = live   # remember which mode worked
                break

        # If still empty, try Contract/available and filter
        if not contracts:
            logger.info("Contract/search returned nothing — trying Contract/available")
            for live in (True, False):
                data = self._post("/Contract/available", {"live": live})
                logger.debug(f"Contract/available response: {data}")
                all_contracts = data.get("contracts", [])
                contracts = [
                    c for c in all_contracts
                    if symbol.upper() in (c.get("name", "") + c.get("description", "")).upper()
                ]
                if contracts:
                    break

        if not contracts:
            raise RuntimeError(f"No contracts found for symbol '{symbol}'")

        # Sort by expiration ascending; pick first active one
        active = [c for c in contracts if c.get("active", True)]
        if not active:
            active = contracts
        active.sort(key=lambda c: c.get("expiration", "9999"))
        contract = active[0]
        logger.info(f"Front-month contract: {contract.get('name')} (id={contract.get('id')})")
        return contract

    # ──────────────────────────────────────────
    #  Historical Data
    # ──────────────────────────────────────────

    def get_bars(self, contract_id, unit, unit_number, start_time, end_time=None, limit=500):
        """
        Fetch historical OHLCV bars.

        Args:
            contract_id: Contract identifier string
            unit: UNIT_MINUTE, UNIT_HOUR, or UNIT_DAY
            unit_number: e.g. 1 for 1-minute bars, 60 for 60-minute
            start_time: datetime (UTC)
            end_time: datetime (UTC), defaults to now
            limit: max bars to return

        Returns:
            List of bar dicts with keys: t, o, h, l, c, v
        """
        if end_time is None:
            end_time = datetime.now(timezone.utc)

        body = {
            "contractId":       contract_id,
            "live":             self.live,
            "startTime":        start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endTime":          end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "unit":             unit,
            "unitNumber":       unit_number,
            "limit":            limit,
            "includePartialBar": False,
        }
        data = self._post("/History/retrieveBars", body)
        logger.debug(f"History response keys: {list(data.keys()) if isinstance(data, dict) else data}")
        # API may return bars under different keys
        bars = data.get("bars") or data.get("data") or data.get("result") or []
        if bars is None:
            bars = []
        logger.debug(f"Fetched {len(bars)} bars (unit={unit}, unitNumber={unit_number})")
        return bars

    def get_1m_bars(self, contract_id, limit=500):
        start = datetime.now(timezone.utc) - timedelta(hours=limit + 10)
        return self.get_bars(contract_id, UNIT_MINUTE, 1, start, limit=limit)

    def get_1h_bars(self, contract_id, limit=300):
        start = datetime.now(timezone.utc) - timedelta(hours=limit + 10)
        return self.get_bars(contract_id, UNIT_HOUR, 1, start, limit=limit)

    def get_daily_bars(self, contract_id, limit=30):
        start = datetime.now(timezone.utc) - timedelta(days=limit + 5)
        return self.get_bars(contract_id, UNIT_DAY, 1, start, limit=limit)

    # ──────────────────────────────────────────
    #  Order Management
    # ──────────────────────────────────────────

    def place_market_order(self, account_id, contract_id, side, size):
        """
        Place a market order.
        side: 0 = Buy, 1 = Sell
        type: 2 = Market
        """
        body = {
            "accountId":  account_id,
            "contractId": contract_id,
            "type":       2,       # Market
            "side":       side,
            "size":       size,
        }
        data = self._post("/Order/place", body)
        logger.info(f"Market order placed: side={side} size={size} → {data}")
        return data

    def place_stop_order(self, account_id, contract_id, side, size, stop_price, tick_size=None):
        """Place a stop-limit order (type=4).
        ProjectX does not support plain StopMarket (type=3).
        We use StopLimit with a 5-tick slippage buffer on the limit side.
        side=1 (Sell stop): limitPrice = stopPrice - 5 ticks
        side=0 (Buy  stop): limitPrice = stopPrice + 5 ticks
        """
        tick = tick_size or DEFAULT_TICK
        sp = _round_tick(stop_price, tick)
        if side == 1:   # sell stop (long SL)
            lp = _round_tick(sp - tick * 5, tick)   # 5-tick buffer below
        else:           # buy stop (short SL)
            lp = _round_tick(sp + tick * 5, tick)   # 5-tick buffer above
        body = {
            "accountId":  account_id,
            "contractId": contract_id,
            "type":       4,       # StopLimit
            "side":       side,
            "size":       size,
            "stopPrice":  sp,
            "limitPrice": lp,
        }
        data = self._post("/Order/place", body)
        logger.info(f"Stop order placed: side={side} stop={sp} limit={lp} → {data}")
        return data

    def place_limit_order(self, account_id, contract_id, side, size, limit_price, tick_size=None):
        """Place a limit order.  type: 1 = Limit"""
        tick = tick_size or DEFAULT_TICK
        lp = _round_tick(limit_price, tick)
        body = {
            "accountId":  account_id,
            "contractId": contract_id,
            "type":       1,       # Limit
            "side":       side,
            "size":       size,
            "limitPrice": lp,
            "stopPrice":  0,
        }
        data = self._post("/Order/place", body)
        logger.info(f"Limit order placed: side={side} limit={lp} → {data}")
        return data

    def cancel_order(self, order_id, account_id=None):
        """Cancel an open order by ID."""
        body = {"orderId": order_id}
        if account_id:
            body["accountId"] = account_id
        data = self._post("/Order/cancel", body)
        success = data.get("success", False)
        if not success:
            logger.warning(f"Cancel order {order_id} failed: {data}")
        else:
            logger.info(f"Order {order_id} cancelled OK")
        return data

    def modify_stop_order(self, account_id, contract_id, order_id, side, size, new_stop_price, tick_size=None):
        """Move a stop order to a new price. Requires the full order fields."""
        tick = tick_size or DEFAULT_TICK
        sp = _round_tick(new_stop_price, tick)
        if side == 1:   # sell stop
            lp = _round_tick(sp - tick * 5, tick)
        else:           # buy stop
            lp = _round_tick(sp + tick * 5, tick)
        data = self._post("/Order/modify", {
            "orderId":    order_id,
            "accountId":  account_id,
            "contractId": contract_id,
            "type":       4,       # StopLimit
            "side":       side,
            "size":       size,
            "stopPrice":  sp,
            "limitPrice": lp,
        })
        logger.debug(f"Stop order {order_id} moved to {sp}")
        return data

    def cancel_all_orders(self, account_id):
        """Cancel every open order for the account."""
        orders = self.get_open_orders(account_id)
        logger.info(f"Found {len(orders)} open orders to cancel.")
        for o in orders:
            oid = o.get("id") or o.get("orderId")
            try:
                self.cancel_order(oid, account_id=account_id)
            except Exception as e:
                logger.warning(f"Could not cancel order {oid}: {e}")
        logger.info(f"Cancel sweep complete ({len(orders)} orders).")

    def close_position(self, account_id, contract_id, position_size):
        """Flatten an open position using the closeContract endpoint."""
        if position_size == 0:
            return
        data = self._post("/Position/closeContract", {
            "accountId":  account_id,
            "contractId": contract_id,
        })
        logger.info(f"Position closed → {data}")
        return data
