"""
Indicator catalog — the menu of primitives a strategy can build from.

Single source of truth for what the platform computes for you. Browse it
before designing a strategy, to see what is already available and which
config knob sits behind each one:

    python -m core.indicator_catalog                  # full catalog
    python -m core.indicator_catalog --category trend # by category
    python -m core.indicator_catalog --tag regime     # by use-case tag
    python -m core.indicator_catalog --markdown > INDICATORS.md

The catalog lists three layers:

    indicator   raw numeric columns    (core/indicators.py)
    feature     derived runtime state  (core/runtime.py)
    risk        account-level machinery (core/risk.py)

It deliberately does NOT list entry filters. A filter is a rule a
strategy applies, not a primitive the platform provides — filters belong
in the strategy that uses them, and listing them centrally is what let
one strategy's fitted thresholds leak into the shared vocabulary.

When you add an indicator or feature, append an entry below so the next
strategy can find it.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import List, Optional

import config as cfg


# ─────────────────────────────────────────────────────────────────────
#  Schema
# ─────────────────────────────────────────────────────────────────────

@dataclass
class CatalogEntry:
    """One indicator, derived feature, or filter."""
    id: str                          # short unique handle: "ema_fast_slow"
    display: str                     # human-readable: "EMA 9 / 20 (5-min)"
    layer: str                       # "indicator" | "feature" | "risk"
    category: str                    # "trend" | "volatility" | "structure" | "volume" |
                                     # "regime" | "session" | "bias" | "risk" | "sizing"
    timeframe: str                   # "5m" | "1h" | "daily" | "runtime" | "n/a"
    columns: List[str]               # df columns produced (empty for pure filters)
    params: List[tuple]              # [(cfg_attr, "description"), ...]
    description: str                 # 1-3 sentence summary
    use_cases: List[str] = field(default_factory=list)
    computed_in: str = ""            # "core/indicators.py" / "core/runtime.py" / strategy file


# ─────────────────────────────────────────────────────────────────────
#  Catalog
# ─────────────────────────────────────────────────────────────────────
# Order: indicators -> derived features -> filters -> risk/sizing.
# Within each layer, grouped roughly by category.

CATALOG: List[CatalogEntry] = [

    # ── INDICATORS (raw numeric values) ───────────────────────────
    CatalogEntry(
        id="ema_fast_slow",
        display="EMA 9 / 20 (5-min)",
        layer="indicator",
        category="trend",
        timeframe="5m",
        columns=["ema_9", "ema_20"],
        params=[
            ("EMA_FAST_LEN", "Length of fast EMA"),
            ("EMA_SLOW_LEN", "Length of slow EMA"),
        ],
        description="Two EMAs on 5-min closes. Forms the trend-stack and "
                    "9/20 cross used by the trend follower. Spread between them "
                    "is also used as a setup-quality filter.",
        use_cases=["trend_filter", "cross_signal", "spread_filter", "stack_alignment"],
        computed_in="core/indicators.py:add_5m_indicators",
    ),
    CatalogEntry(
        id="ema_50_1h",
        display="EMA 50 (1-hour)",
        layer="indicator",
        category="trend",
        timeframe="1h",
        columns=["ema_50_1h"],
        params=[("EMA_50_1H_LEN", "Length of 1H EMA used as HTF trend filter")],
        description="Higher-timeframe trend gate. Mapped onto every 5-min bar via "
                    "merge_asof. Long entries require close > ema_50_1h, short "
                    "entries require close < ema_50_1h.",
        use_cases=["htf_trend_filter"],
        computed_in="core/indicators.py:add_1h_indicators",
    ),
    CatalogEntry(
        id="atr",
        display="ATR (fast 5, slow 20)",
        layer="indicator",
        category="volatility",
        timeframe="5m",
        columns=["atr_fast", "atr_slow"],
        params=[
            ("ATR_FAST_LEN", "Length of fast ATR (volatility expansion check)"),
            ("ATR_SLOW_LEN", "Length of slow ATR (used for SL distance)"),
        ],
        description="Two ATR lengths. atr_slow drives stop-loss distance and the "
                    "ATR-based dead zone. atr_fast / atr_slow ratio is the "
                    "volatility-expansion filter.",
        use_cases=["sl_distance", "vol_expansion_filter", "trail_distance"],
        computed_in="core/indicators.py:add_5m_indicators",
    ),
    CatalogEntry(
        id="keltner",
        display="Keltner Channels (KC1 + KC2)",
        layer="indicator",
        category="structure",
        timeframe="5m",
        columns=["kc_basis", "kc_upper1", "kc_lower1", "kc_upper2", "kc_lower2",
                 "kc_upper_tp", "kc_lower_tp", "kc_bw", "kc_bw_ma"],
        params=[
            ("KC_LEN", "Length of KC EMA basis"),
            ("KC_ATR_LEN", "Length of ATR used inside KC computation"),
            ("KC_MULT1", "Inner band multiplier"),
            ("KC_MULT2", "Outer band multiplier"),
            ("KC_MULT_TP", "Third band multiplier, available as a separate target reference"),
            ("KC_BW_LOOKBACK", "Bars used to compute the avg KC bandwidth"),
        ],
        description="Three Keltner band widths around an EMA basis. Commonly used "
                    "for target placement and trailing stops; the bandwidth "
                    "(kc_bw vs kc_bw_ma) also serves as a squeeze detector.",
        use_cases=["target_placement", "trail_stop", "regime_squeeze"],
        computed_in="core/indicators.py:add_5m_indicators",
    ),
    CatalogEntry(
        id="vwap",
        display="VWAP (intraday, Globex reset)",
        layer="indicator",
        category="trend",
        timeframe="5m",
        columns=["vwap"],
        params=[],  # VWAP has no tunable knobs in current implementation
        description="Intraday VWAP that resets at the CME Globex session open "
                    "(18:00 ET). Used as a directional gate — long entries "
                    "require close > vwap, short entries require close < vwap.",
        use_cases=["intraday_trend_filter", "mean_reversion_anchor"],
        computed_in="core/indicators.py:add_5m_indicators",
    ),
    CatalogEntry(
        id="volume_ma",
        display="Volume SMA(20)",
        layer="indicator",
        category="volume",
        timeframe="5m",
        columns=["volume_ma"],
        params=[("VOLUME_MA_LEN", "Window length for the volume SMA")],
        description="Rolling SMA of bar volume. Used by the volume-confirmation "
                    "filter (entry bar's volume must exceed this MA).",
        use_cases=["volume_confirmation"],
        computed_in="core/indicators.py:add_5m_indicators",
    ),
    CatalogEntry(
        id="candle_body",
        display="Candle body metrics",
        layer="indicator",
        category="structure",
        timeframe="5m",
        columns=["body_top", "body_bot", "body_mid"],
        params=[],
        description="max(open,close), min(open,close), midpoint. Used by the "
                    "TP_TRIGGER_MODE='body_*' variants where a TP triggers on "
                    "candle body crossing the band, not just a wick.",
        use_cases=["body_vs_wick_logic"],
        computed_in="core/indicators.py:add_5m_indicators",
    ),
    CatalogEntry(
        id="yesterday_hlc",
        display="Yesterday's High / Low / Close (daily)",
        layer="indicator",
        category="structure",
        timeframe="daily",
        columns=["yest_high", "yest_low", "yest_close"],
        params=[],
        description="Previous-day OHLC anchored on the daily frame. Available "
                    "to strategies that need overnight reference levels (gap "
                    "detection, daily-range breakouts, etc.).",
        use_cases=["gap_detection", "daily_breakout", "overnight_reference"],
        computed_in="core/indicators.py:add_daily_indicators",
    ),

    # ── DERIVED RUNTIME FEATURES (precomputed columns) ────────────
    CatalogEntry(
        id="hour_pt",
        display="Pacific-Time hour",
        layer="feature",
        category="session",
        timeframe="runtime",
        columns=["hour_pt"],
        params=[],
        description="The bar's hour in America/Vancouver (Pacific Time). Drives "
                    "the per-symbol time-of-day filter.",
        use_cases=["time_filter"],
        computed_in="core/runtime.py:precompute_runtime_features",
    ),
    CatalogEntry(
        id="in_session",
        display="Session-window mask",
        layer="feature",
        category="session",
        timeframe="runtime",
        columns=["in_session", "is_first_half"],
        params=[
            ("USE_SESSION", "Master switch for session masking"),
            ("SESSION_START_PT", "Session window start (PT)"),
            ("SESSION_END_PT", "Session window end (PT)"),
        ],
        description="Boolean flag for whether the bar falls inside the session "
                    "window, plus a first-half / second-half split (used by the "
                    "locked bias). Per-instrument overrides live in instruments.py.",
        use_cases=["session_filter", "bias_anchor"],
        computed_in="core/runtime.py:precompute_runtime_features",
    ),
    CatalogEntry(
        id="locked_bias",
        display="Locked session-half directional bias",
        layer="feature",
        category="bias",
        timeframe="runtime",
        columns=["bias_direction", "fh_close", "prev_sh_close"],
        params=[("DEAD_ZONE_ATR_MULT", "Half-width of the no-trade dead zone, in ATR(slow) units")],
        description="+1 long / -1 short / 0 none, locked at the first bar of "
                    "each session-half against the previous half's close. Anchor: "
                    "first half → yesterday's second-half close; second half "
                    "→ today's first-half close. ATR-based dead zone keeps "
                    "the bias stable across vol regimes.",
        use_cases=["directional_gate", "session_bias"],
        computed_in="core/runtime.py:precompute_runtime_features",
    ),
    CatalogEntry(
        id="ema_cross_freshness",
        display="9/20 cross freshness",
        layer="feature",
        category="trend",
        timeframe="runtime",
        columns=["bars_since_bull_cross", "bars_since_bear_cross"],
        params=[],
        description="Bars elapsed since the last bullish or bearish 9/20 EMA "
                    "cross. Used by the freshness filter to require entries to "
                    "happen near the start of a trend, not deep into it.",
        use_cases=["fresh_cross_filter", "trend_age_check"],
        computed_in="core/runtime.py:precompute_runtime_features",
    ),
    CatalogEntry(
        id="kc_squeeze",
        display="KC bandwidth squeeze",
        layer="feature",
        category="regime",
        timeframe="5m",
        columns=["kc_squeeze"],
        params=[
            ("USE_KC_BANDWIDTH", "Master switch for the squeeze detector"),
            ("KC_BW_LOOKBACK", "Lookback for the bandwidth average"),
            ("KC_BW_THRESH", "Squeeze fires when current width / avg width <= this"),
        ],
        description="Boolean. True when the current KC width is below threshold "
                    "fraction of its rolling average — i.e. a low-volatility "
                    "compression. Half of the range-regime detector.",
        use_cases=["squeeze_detection", "regime_filter"],
        computed_in="core/indicators.py:add_5m_indicators",
    ),
    CatalogEntry(
        id="kc_slope_flat",
        display="KC slope flatness",
        layer="feature",
        category="regime",
        timeframe="5m",
        columns=["kc_slope_flat"],
        params=[
            ("USE_KC_SLOPE", "Master switch for the slope detector"),
            ("KC_SLOPE_BARS", "Lookback for measuring KC basis slope"),
            ("KC_SLOPE_MIN_ATR", "Slope below this many ATRs over the lookback = flat"),
        ],
        description="Boolean. True when the KC basis has barely moved over the "
                    "slope window (drift < N × ATR). The other half of the "
                    "range-regime detector.",
        use_cases=["chop_detection", "regime_filter"],
        computed_in="core/indicators.py:add_5m_indicators",
    ),
    CatalogEntry(
        id="is_range",
        display="Range regime flag",
        layer="feature",
        category="regime",
        timeframe="5m",
        columns=["is_range"],
        params=[],  # combination of kc_squeeze + kc_slope_flat
        description="kc_squeeze OR kc_slope_flat. Strategies that should only "
                    "trade trending markets read this and skip entries when "
                    "True. The engine exposes it to strategies as ctx[\"range_blocked\"].",
        use_cases=["regime_filter", "trend_only_strategies"],
        computed_in="core/indicators.py:add_5m_indicators",
    ),

    # ── ENTRY FILTERS (gate logic in strategy code) ───────────────
    CatalogEntry(
        id="kill_switch",
        display="Daily / consecutive-loss kill switches",
        layer="risk",
        category="risk",
        timeframe="n/a",
        columns=[],
        params=[
            ("USE_KILL_SWITCH", "Daily PnL kill switch on/off"),
            ("KILL_DOLLAR", "Daily loss limit ($)"),
            ("USE_CONSEC3_KILL", "3-loss pause on/off"),
            ("CONSEC3_MAX_LOSS", "Total loss across 3 consecutive entries that triggers pause"),
            ("USE_CONSEC5_KILL", "5-loss pause on/off"),
            ("CONSEC5_MAX_LOSS", "Total loss across 5 consecutive entries that triggers pause"),
        ],
        description="Layered automatic stop-trading triggers. Daily kill closes "
                    "all open slots and blocks new entries for the day. Consec-3 "
                    "and consec-5 add 15-30 min and 120-180 min pauses respectively.",
        use_cases=["risk_caps"],
        computed_in="core/risk.py:RiskManager",
    ),
]


# ─────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────

LAYER_ORDER = ("indicator", "feature", "risk")
CATEGORY_ICONS = {
    "trend":      "[trend]",
    "volatility": "[vol]",
    "structure":  "[struct]",
    "volume":     "[vol-z]",
    "regime":     "[regime]",
    "session":    "[session]",
    "bias":       "[bias]",
    "risk":       "[risk]",
    "sizing":     "[sizing]",
}


def _resolve(attr: str):
    """Return current value of a config attribute, or '<unset>'."""
    return getattr(cfg, attr, "<unset>")


def filter_entries(category: Optional[str] = None,
                   layer: Optional[str] = None,
                   tag: Optional[str] = None,
                   timeframe: Optional[str] = None) -> List[CatalogEntry]:
    out = list(CATALOG)
    if category:
        out = [e for e in out if e.category == category]
    if layer:
        out = [e for e in out if e.layer == layer]
    if tag:
        out = [e for e in out if tag in e.use_cases]
    if timeframe:
        out = [e for e in out if e.timeframe == timeframe]
    return out


def print_catalog(entries: Optional[List[CatalogEntry]] = None) -> None:
    entries = entries if entries is not None else list(CATALOG)
    if not entries:
        print("(no matches)")
        return

    # Group by layer, then category
    grouped: dict = {}
    for e in entries:
        grouped.setdefault(e.layer, {}).setdefault(e.category, []).append(e)

    for layer in LAYER_ORDER:
        if layer not in grouped:
            continue
        print()
        print("=" * 90)
        print(f"  {layer.upper()}S")
        print("=" * 90)
        for cat in sorted(grouped[layer]):
            tag = CATEGORY_ICONS.get(cat, f"[{cat}]")
            print(f"\n  {tag} {cat}")
            print(f"  {'-' * 84}")
            for e in grouped[layer][cat]:
                tf = f"[{e.timeframe}]" if e.timeframe != "n/a" else ""
                print(f"  - {e.display}  {tf}")
                print(f"      id          : {e.id}")
                if e.columns:
                    print(f"      columns     : {', '.join(e.columns)}")
                if e.params:
                    for attr, desc in e.params:
                        val = _resolve(attr)
                        val_repr = repr(val) if not isinstance(val, (int, float, bool, str)) else val
                        print(f"      {attr:<22} = {val_repr}     // {desc}")
                if e.use_cases:
                    print(f"      use cases   : {', '.join(e.use_cases)}")
                print(f"      computed in : {e.computed_in}")


def to_markdown() -> str:
    """Render the full catalog as INDICATORS.md content."""
    lines = []
    lines.append("# Indicator Catalog")
    lines.append("")
    lines.append("The menu of primitives a strategy can build from: every indicator, "
                 "derived feature and risk control the platform computes for you. "
                 "Generated from [core/indicator_catalog.py](core/indicator_catalog.py); "
                 "to add one, implement it in `core/indicators.py` (or "
                 "`core/runtime.py`) and append a `CatalogEntry` to that module.")
    lines.append("")
    lines.append("Re-generate this file: `python -m core.indicator_catalog --markdown > INDICATORS.md`")
    lines.append("")
    lines.append("Layers:")
    lines.append("- **indicator**: raw numeric columns written to the bar frame (EMA, ATR, KC bands, VWAP, ...).")
    lines.append("- **feature**: derived runtime state (session mask, EMA-cross freshness, optional locked bias, ...).")
    lines.append("- **risk**: account-level machinery enforced by the engine, independent of any strategy.")
    lines.append("")
    lines.append("Entry filters are deliberately absent. A filter is a rule a strategy "
                 "applies, not a primitive the platform supplies - it belongs in the "
                 "strategy module that uses it.")
    lines.append("")

    # Group by layer -> category
    grouped: dict = {}
    for e in CATALOG:
        grouped.setdefault(e.layer, {}).setdefault(e.category, []).append(e)

    for layer in LAYER_ORDER:
        if layer not in grouped:
            continue
        lines.append(f"## {layer.title()}s")
        lines.append("")
        for cat in sorted(grouped[layer]):
            lines.append(f"### {cat.title()}")
            lines.append("")
            lines.append("| Display | id | timeframe | columns | use cases |")
            lines.append("|---|---|---|---|---|")
            for e in grouped[layer][cat]:
                cols = "`" + "`, `".join(e.columns) + "`" if e.columns else "—"
                tags = ", ".join(e.use_cases) if e.use_cases else "—"
                tf = e.timeframe if e.timeframe != "n/a" else "—"
                lines.append(f"| {e.display} | `{e.id}` | {tf} | {cols} | {tags} |")
            lines.append("")
            for e in grouped[layer][cat]:
                lines.append(f"#### `{e.id}` — {e.display}")
                lines.append("")
                lines.append(e.description)
                lines.append("")
                if e.params:
                    lines.append("| config attr | current value | description |")
                    lines.append("|---|---|---|")
                    for attr, desc in e.params:
                        val = _resolve(attr)
                        lines.append(f"| `{attr}` | `{val}` | {desc} |")
                    lines.append("")
                lines.append(f"_Computed in: [{e.computed_in}]({e.computed_in.split(':')[0]})_")
                lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Browse the indicator + filter catalog.")
    p.add_argument("--category", help="Filter by category (trend / volatility / structure / "
                                      "volume / regime / session / bias / risk / sizing)")
    p.add_argument("--layer", help="Filter by layer (indicator / feature / filter)")
    p.add_argument("--tag", help="Filter by use-case tag (e.g. trend_filter, regime_filter)")
    p.add_argument("--timeframe", help="Filter by timeframe (5m / 1h / daily / runtime)")
    p.add_argument("--markdown", action="store_true",
                   help="Emit catalog as INDICATORS.md content (filters apply)")
    args = p.parse_args()

    entries = filter_entries(
        category=args.category, layer=args.layer,
        tag=args.tag, timeframe=args.timeframe,
    )

    if args.markdown:
        print(to_markdown())
        return

    print_catalog(entries)
    print()
    print(f"  {len(entries)} entries shown (out of {len(CATALOG)} total).")


if __name__ == "__main__":
    main()
