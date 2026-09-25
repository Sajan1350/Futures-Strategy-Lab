# ─────────────────────────────────────────────────────────────────
#  Platform configuration
#
#  This file holds ONLY parameters that belong to shared machinery:
#  indicator lengths, the session window, risk limits and data sizes.
#
#  It deliberately contains NO strategy parameters. Entry thresholds,
#  stop multiples, target placement, sizing ladders and any fitted
#  per-symbol values belong to the strategy that uses them — keep them
#  in that strategy's own module so a strategy can be read, reasoned
#  about and deleted in one piece.
# ─────────────────────────────────────────────────────────────────

# ── EMA lengths (5-min chart) ──
EMA_FAST_LEN      = 9
EMA_SLOW_LEN      = 20
EMA_50_1H_LEN     = 50      # 1H higher-timeframe EMA

# ── Keltner Channels ──
KC_LEN            = 20      # KC EMA basis length
KC_ATR_LEN        = 10      # ATR length used inside the KC calculation
KC_MULT1          = 2.0     # inner band multiplier
KC_MULT2          = 3.0     # outer band multiplier
KC_MULT_TP        = 3.0     # third band, available as a target reference

# ── ATR lengths ──
ATR_FAST_LEN      = 5       # fast ATR (volatility expansion measurement)
ATR_SLOW_LEN      = 20      # slow ATR (stop distance, normalisation)

# ── Volume ──
VOLUME_MA_LEN     = 20      # rolling volume SMA length

# ── Range-regime detection (computed as a feature; using it is optional) ──
USE_KC_BANDWIDTH  = True
KC_BW_LOOKBACK    = 50
KC_BW_THRESH      = 0.6     # KC width < 60% of lookback avg -> squeeze
USE_KC_SLOPE      = True
KC_SLOPE_BARS     = 20
KC_SLOPE_MIN_ATR  = 0.3     # basis move < 0.3 x ATR over N bars -> flat

# ── Session window (Pacific Time; per-instrument overrides in instruments.py) ──
USE_SESSION       = True
SESSION_START_PT  = "04:00"
SESSION_END_PT    = "15:00"

# ── Locked-per-half directional bias (opt-in feature in core/runtime.py) ──
# Only used when precompute_runtime_features(..., with_locked_bias=True).
DEAD_ZONE_ATR_MULT = 1.0

# ── Default position size ──
# Starting size for a strategy slot. A strategy that sizes dynamically
# overrides this per entry via EntrySignal.size.
DEFAULT_TRADE_SIZE = 1

# ── Daily loss kill switch ──
USE_KILL_SWITCH   = True
KILL_MODE         = "Dollar"
KILL_DOLLAR       = 1000.0
KILL_PERCENT      = 2.0

# ── Consecutive loss kill switches ──
USE_CONSEC3_KILL   = True
CONSEC3_MAX_LOSS   = 450.0
CONSEC3_MIN_PAUSE  = 15     # minutes
CONSEC3_MAX_PAUSE  = 30

USE_CONSEC5_KILL   = True
CONSEC5_MAX_LOSS   = 750.0
CONSEC5_MIN_PAUSE  = 120    # minutes
CONSEC5_MAX_PAUSE  = 180

# ── Bars fetched at startup (live trading) ──
HISTORY_BARS_1M   = 500
HISTORY_BARS_1H   = 300
HISTORY_BARS_1D   = 30
