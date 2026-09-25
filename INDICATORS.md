# Indicator Catalog

The menu of primitives a strategy can build from: every indicator, derived feature and risk control the platform computes for you. Generated from [core/indicator_catalog.py](core/indicator_catalog.py); to add one, implement it in `core/indicators.py` (or `core/runtime.py`) and append a `CatalogEntry` to that module.

Re-generate this file: `python -m core.indicator_catalog --markdown > INDICATORS.md`

Layers:
- **indicator**: raw numeric columns written to the bar frame (EMA, ATR, KC bands, VWAP, ...).
- **feature**: derived runtime state (session mask, EMA-cross freshness, optional locked bias, ...).
- **risk**: account-level machinery enforced by the engine, independent of any strategy.

Entry filters are deliberately absent. A filter is a rule a strategy applies, not a primitive the platform supplies - it belongs in the strategy module that uses it.

## Indicators

### Structure

| Display | id | timeframe | columns | use cases |
|---|---|---|---|---|
| Keltner Channels (KC1 + KC2) | `keltner` | 5m | `kc_basis`, `kc_upper1`, `kc_lower1`, `kc_upper2`, `kc_lower2`, `kc_upper_tp`, `kc_lower_tp`, `kc_bw`, `kc_bw_ma` | target_placement, trail_stop, regime_squeeze |
| Candle body metrics | `candle_body` | 5m | `body_top`, `body_bot`, `body_mid` | body_vs_wick_logic |
| Yesterday's High / Low / Close (daily) | `yesterday_hlc` | daily | `yest_high`, `yest_low`, `yest_close` | gap_detection, daily_breakout, overnight_reference |

#### `keltner` — Keltner Channels (KC1 + KC2)

Three Keltner band widths around an EMA basis. Commonly used for target placement and trailing stops; the bandwidth (kc_bw vs kc_bw_ma) also serves as a squeeze detector.

| config attr | current value | description |
|---|---|---|
| `KC_LEN` | `20` | Length of KC EMA basis |
| `KC_ATR_LEN` | `10` | Length of ATR used inside KC computation |
| `KC_MULT1` | `2.0` | Inner band multiplier |
| `KC_MULT2` | `3.0` | Outer band multiplier |
| `KC_MULT_TP` | `3.0` | Third band multiplier, available as a separate target reference |
| `KC_BW_LOOKBACK` | `50` | Bars used to compute the avg KC bandwidth |

_Computed in: [core/indicators.py:add_5m_indicators](core/indicators.py)_

#### `candle_body` — Candle body metrics

max(open,close), min(open,close), midpoint. Used by the TP_TRIGGER_MODE='body_*' variants where a TP triggers on candle body crossing the band, not just a wick.

_Computed in: [core/indicators.py:add_5m_indicators](core/indicators.py)_

#### `yesterday_hlc` — Yesterday's High / Low / Close (daily)

Previous-day OHLC anchored on the daily frame. Available to strategies that need overnight reference levels (gap detection, daily-range breakouts, etc.).

_Computed in: [core/indicators.py:add_daily_indicators](core/indicators.py)_

### Trend

| Display | id | timeframe | columns | use cases |
|---|---|---|---|---|
| EMA 9 / 20 (5-min) | `ema_fast_slow` | 5m | `ema_9`, `ema_20` | trend_filter, cross_signal, spread_filter, stack_alignment |
| EMA 50 (1-hour) | `ema_50_1h` | 1h | `ema_50_1h` | htf_trend_filter |
| VWAP (intraday, Globex reset) | `vwap` | 5m | `vwap` | intraday_trend_filter, mean_reversion_anchor |

#### `ema_fast_slow` — EMA 9 / 20 (5-min)

Two EMAs on 5-min closes. Forms the trend-stack and 9/20 cross used by the trend follower. Spread between them is also used as a setup-quality filter.

| config attr | current value | description |
|---|---|---|
| `EMA_FAST_LEN` | `9` | Length of fast EMA |
| `EMA_SLOW_LEN` | `20` | Length of slow EMA |

_Computed in: [core/indicators.py:add_5m_indicators](core/indicators.py)_

#### `ema_50_1h` — EMA 50 (1-hour)

Higher-timeframe trend gate. Mapped onto every 5-min bar via merge_asof. Long entries require close > ema_50_1h, short entries require close < ema_50_1h.

| config attr | current value | description |
|---|---|---|
| `EMA_50_1H_LEN` | `50` | Length of 1H EMA used as HTF trend filter |

_Computed in: [core/indicators.py:add_1h_indicators](core/indicators.py)_

#### `vwap` — VWAP (intraday, Globex reset)

Intraday VWAP that resets at the CME Globex session open (18:00 ET). Used as a directional gate — long entries require close > vwap, short entries require close < vwap.

_Computed in: [core/indicators.py:add_5m_indicators](core/indicators.py)_

### Volatility

| Display | id | timeframe | columns | use cases |
|---|---|---|---|---|
| ATR (fast 5, slow 20) | `atr` | 5m | `atr_fast`, `atr_slow` | sl_distance, vol_expansion_filter, trail_distance |

#### `atr` — ATR (fast 5, slow 20)

Two ATR lengths. atr_slow drives stop-loss distance and the ATR-based dead zone. atr_fast / atr_slow ratio is the volatility-expansion filter.

| config attr | current value | description |
|---|---|---|
| `ATR_FAST_LEN` | `5` | Length of fast ATR (volatility expansion check) |
| `ATR_SLOW_LEN` | `20` | Length of slow ATR (used for SL distance) |

_Computed in: [core/indicators.py:add_5m_indicators](core/indicators.py)_

### Volume

| Display | id | timeframe | columns | use cases |
|---|---|---|---|---|
| Volume SMA(20) | `volume_ma` | 5m | `volume_ma` | volume_confirmation |

#### `volume_ma` — Volume SMA(20)

Rolling SMA of bar volume. Used by the volume-confirmation filter (entry bar's volume must exceed this MA).

| config attr | current value | description |
|---|---|---|
| `VOLUME_MA_LEN` | `20` | Window length for the volume SMA |

_Computed in: [core/indicators.py:add_5m_indicators](core/indicators.py)_

## Features

### Bias

| Display | id | timeframe | columns | use cases |
|---|---|---|---|---|
| Locked session-half directional bias | `locked_bias` | runtime | `bias_direction`, `fh_close`, `prev_sh_close` | directional_gate, session_bias |

#### `locked_bias` — Locked session-half directional bias

+1 long / -1 short / 0 none, locked at the first bar of each session-half against the previous half's close. Anchor: first half → yesterday's second-half close; second half → today's first-half close. ATR-based dead zone keeps the bias stable across vol regimes.

| config attr | current value | description |
|---|---|---|
| `DEAD_ZONE_ATR_MULT` | `1.0` | Half-width of the no-trade dead zone, in ATR(slow) units |

_Computed in: [core/runtime.py:precompute_runtime_features](core/runtime.py)_

### Regime

| Display | id | timeframe | columns | use cases |
|---|---|---|---|---|
| KC bandwidth squeeze | `kc_squeeze` | 5m | `kc_squeeze` | squeeze_detection, regime_filter |
| KC slope flatness | `kc_slope_flat` | 5m | `kc_slope_flat` | chop_detection, regime_filter |
| Range regime flag | `is_range` | 5m | `is_range` | regime_filter, trend_only_strategies |

#### `kc_squeeze` — KC bandwidth squeeze

Boolean. True when the current KC width is below threshold fraction of its rolling average — i.e. a low-volatility compression. Half of the range-regime detector.

| config attr | current value | description |
|---|---|---|
| `USE_KC_BANDWIDTH` | `True` | Master switch for the squeeze detector |
| `KC_BW_LOOKBACK` | `50` | Lookback for the bandwidth average |
| `KC_BW_THRESH` | `0.6` | Squeeze fires when current width / avg width <= this |

_Computed in: [core/indicators.py:add_5m_indicators](core/indicators.py)_

#### `kc_slope_flat` — KC slope flatness

Boolean. True when the KC basis has barely moved over the slope window (drift < N × ATR). The other half of the range-regime detector.

| config attr | current value | description |
|---|---|---|
| `USE_KC_SLOPE` | `True` | Master switch for the slope detector |
| `KC_SLOPE_BARS` | `20` | Lookback for measuring KC basis slope |
| `KC_SLOPE_MIN_ATR` | `0.3` | Slope below this many ATRs over the lookback = flat |

_Computed in: [core/indicators.py:add_5m_indicators](core/indicators.py)_

#### `is_range` — Range regime flag

kc_squeeze OR kc_slope_flat. Strategies that should only trade trending markets read this and skip entries when True. The engine exposes it to strategies as ctx["range_blocked"].

_Computed in: [core/indicators.py:add_5m_indicators](core/indicators.py)_

### Session

| Display | id | timeframe | columns | use cases |
|---|---|---|---|---|
| Pacific-Time hour | `hour_pt` | runtime | `hour_pt` | time_filter |
| Session-window mask | `in_session` | runtime | `in_session`, `is_first_half` | session_filter, bias_anchor |

#### `hour_pt` — Pacific-Time hour

The bar's hour in America/Vancouver (Pacific Time). Drives the per-symbol time-of-day filter.

_Computed in: [core/runtime.py:precompute_runtime_features](core/runtime.py)_

#### `in_session` — Session-window mask

Boolean flag for whether the bar falls inside the session window, plus a first-half / second-half split (used by the locked bias). Per-instrument overrides live in instruments.py.

| config attr | current value | description |
|---|---|---|
| `USE_SESSION` | `True` | Master switch for session masking |
| `SESSION_START_PT` | `04:00` | Session window start (PT) |
| `SESSION_END_PT` | `15:00` | Session window end (PT) |

_Computed in: [core/runtime.py:precompute_runtime_features](core/runtime.py)_

### Trend

| Display | id | timeframe | columns | use cases |
|---|---|---|---|---|
| 9/20 cross freshness | `ema_cross_freshness` | runtime | `bars_since_bull_cross`, `bars_since_bear_cross` | fresh_cross_filter, trend_age_check |

#### `ema_cross_freshness` — 9/20 cross freshness

Bars elapsed since the last bullish or bearish 9/20 EMA cross. Used by the freshness filter to require entries to happen near the start of a trend, not deep into it.

_Computed in: [core/runtime.py:precompute_runtime_features](core/runtime.py)_

## Risks

### Risk

| Display | id | timeframe | columns | use cases |
|---|---|---|---|---|
| Daily / consecutive-loss kill switches | `kill_switch` | — | — | risk_caps |

#### `kill_switch` — Daily / consecutive-loss kill switches

Layered automatic stop-trading triggers. Daily kill closes all open slots and blocks new entries for the day. Consec-3 and consec-5 add 15-30 min and 120-180 min pauses respectively.

| config attr | current value | description |
|---|---|---|
| `USE_KILL_SWITCH` | `True` | Daily PnL kill switch on/off |
| `KILL_DOLLAR` | `1000.0` | Daily loss limit ($) |
| `USE_CONSEC3_KILL` | `True` | 3-loss pause on/off |
| `CONSEC3_MAX_LOSS` | `450.0` | Total loss across 3 consecutive entries that triggers pause |
| `USE_CONSEC5_KILL` | `True` | 5-loss pause on/off |
| `CONSEC5_MAX_LOSS` | `750.0` | Total loss across 5 consecutive entries that triggers pause |

_Computed in: [core/risk.py:RiskManager](core/risk.py)_

