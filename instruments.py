"""
Instrument definitions for multi-instrument trading.
Each instrument has its own tick size, point value, and default parameters.
"""


INSTRUMENT_DEFS = {
    "MGC": {
        "name": "Micro Gold",
        "symbol": "MGC",
        "tick_size": 0.10,
        "point_value": 10,       # $10 per 1-point move per contract
        "session_start_pt": "05:00",
        "session_end_pt": "10:00",
        "sl_multiplier": 2.5,
        "tp_multiplier": 4.0,
        "long_min_score": 11.0,
        "short_min_score": 11.5,
        "trade_size": 1,
        "max_loss_per_trade": 150.0,
        "daily_loss_limit": 500.0,
    },
    "MNQ": {
        "name": "Micro Nasdaq",
        "symbol": "MNQ",
        "tick_size": 0.25,
        "point_value": 2,        # $2 per 1-point move per contract
        "session_start_pt": "06:30",
        "session_end_pt": "13:00",
        "sl_multiplier": 2.0,
        "tp_multiplier": 4.0,
        "long_min_score": 11.0,
        "short_min_score": 11.5,
        "trade_size": 1,
        "max_loss_per_trade": 150.0,
        "daily_loss_limit": 500.0,
    },
    "MES": {
        "name": "Micro S&P 500",
        "symbol": "MES",
        "tick_size": 0.25,
        "point_value": 5,        # $5 per 1-point move per contract
        "session_start_pt": "06:30",
        "session_end_pt": "13:00",
        "sl_multiplier": 2.0,
        "tp_multiplier": 4.0,
        "long_min_score": 11.0,
        "short_min_score": 11.5,
        "trade_size": 1,
        "max_loss_per_trade": 150.0,
        "daily_loss_limit": 500.0,
    },
    "MCL": {
        "name": "Micro Crude Oil",
        "symbol": "MCL",
        "tick_size": 0.01,
        "point_value": 100,      # $100 per 1-point move per contract
        "session_start_pt": "06:00",
        "session_end_pt": "13:00",
        "sl_multiplier": 2.0,
        "tp_multiplier": 4.0,
        "long_min_score": 11.0,
        "short_min_score": 11.5,
        "trade_size": 1,
        "max_loss_per_trade": 150.0,
        "daily_loss_limit": 500.0,
    },
}

# Global risk settings
GLOBAL_KILL_SWITCH = 1000.0      # max daily loss across ALL instruments
MAX_SIMULTANEOUS = 2             # max instruments with open positions at once


def get_instrument(symbol: str) -> dict:
    """Get instrument config by symbol."""
    return INSTRUMENT_DEFS.get(symbol.upper(), None)


def get_all_symbols() -> list:
    return list(INSTRUMENT_DEFS.keys())
