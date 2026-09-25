"""
Panama back-adjustment for futures continuous contracts.

Reads each raw per-contract .dbn.zst file as a single real contract
(identified by its Databento instrument_id), sorts contracts by their
true expiration (last trading minute), assigns each contract a contiguous
front-month window between the previous contract's expiration and its own,
computes roll spreads at the last common timestamp between consecutive
front contracts, and writes additively back-adjusted parquet files to:

    data/parquet/{SYMBOL}_adj.parquet

The non-adjusted originals at data/parquet/{SYMBOL}.parquet are left
untouched so future work can experiment with alternate roll methods
(volume-based front month, ratio adjustment, raw per-contract trading).

Run:
    python -m scripts.back_adjust
"""

import re
import sys
import logging
from pathlib import Path
from dataclasses import dataclass

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("back_adjust")

PROJECT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT / "data" / "raw"
OUT_DIR = PROJECT / "data" / "parquet"

SYMBOL_MAP = {
    "MGC": "MGC", "MNQ": "MNQ", "MES": "MES",
    "MCL": "MCL", "MCLF": "MCL", "MCLE": "MCL",
}

MONTH_CODE_ORDER = "FGHJKMNQUVXZ"  # F=Jan ... Z=Dec
MONTH_CODE_IDX = {c: i for i, c in enumerate(MONTH_CODE_ORDER)}


@dataclass
class ContractInstance:
    contract_id: str             # unique per real contract (instrument_id-derived)
    root: str                    # e.g. "MGC"
    month_code: str              # e.g. "G"
    file_code: str               # original file's symbol code (e.g. "MGCG0")
    first_bar_ts: pd.Timestamp
    last_bar_ts: pd.Timestamp    # actual last trading bar
    df: pd.DataFrame             # OHLCV indexed by tz-aware UTC timestamp
    expiry_proxy: pd.Timestamp = None   # set after load: best estimate of "stops being front"
    is_active: bool = False             # True if still trading at data end


ACTIVE_THRESHOLD_DAYS = 3


def compute_expiry_proxy(file_code: str, last_bar_ts: pd.Timestamp,
                         data_end_ts: pd.Timestamp):
    """Return (expiry_proxy_ts, is_active).

    Always derives the scheduled expiration from the symbol code so that
    contracts sort in correct calendar order regardless of how sparse their
    data is. Falls back to last_bar_ts only when the symbol code can't be
    parsed.

    Scheduled expiry uses day 28 at 23:59 UTC as a stable in-month
    placeholder — the exact day doesn't matter for sort ordering.
    """
    is_active = (data_end_ts - last_bar_ts) <= pd.Timedelta(days=ACTIVE_THRESHOLD_DAYS)

    parsed = parse_contract_code(file_code)
    if parsed is None:
        return last_bar_ts, is_active

    _, month_code, year_digit_str = parsed
    year_digit = int(year_digit_str)
    decade = (last_bar_ts.year // 10) * 10
    year = decade + year_digit
    if year < last_bar_ts.year:
        year += 10
    month_int = MONTH_CODE_IDX[month_code] + 1
    try:
        scheduled = pd.Timestamp(year=year, month=month_int, day=28,
                                 hour=23, minute=59, tz="UTC")
        return scheduled, is_active
    except Exception:
        return last_bar_ts, is_active


def classify_file(filename: str):
    """Return (symbol, contract_code) or (None, None) for spreads/unknown."""
    name_lower = filename.lower()
    marker = "ohlcv-1m."
    idx_start = name_lower.find(marker)
    if idx_start < 0:
        return None, None
    idx_start += len(marker)
    idx_end = name_lower.find(".dbn", idx_start)
    if idx_end < 0:
        return None, None
    instrument_seg = filename[idx_start:idx_end]
    if "-" in instrument_seg:
        return None, None  # spread
    seg_upper = instrument_seg.upper()
    for root in sorted(SYMBOL_MAP.keys(), key=lambda x: -len(x)):
        if seg_upper.startswith(root):
            return SYMBOL_MAP[root], seg_upper
    return None, None


def parse_contract_code(code: str):
    """Parse e.g. 'MGCG0' -> ('MGC', 'G', '0')."""
    m = re.match(r"^([A-Z]+?)([FGHJKMNQUVXZ])(\d+)$", code)
    if not m:
        return None
    return m.groups()


def load_symbol_instances(symbol: str):
    """Load every contract instance for a symbol from raw .dbn.zst files.

    Each file is treated as one real contract. If a file ever contains
    multiple instrument_ids (rare but possible), each id becomes a
    separate ContractInstance.
    """
    try:
        import databento as db
    except ImportError:
        log.error("databento not installed. Run: pip install databento")
        sys.exit(1)

    files = sorted(RAW_DIR.rglob("*.dbn.zst"))
    matched = [f for f in files if classify_file(f.name)[0] == symbol]
    log.info(f"[{symbol}] scanning {len(matched)} matching contract files...")

    instances = []
    for f in matched:
        _, file_code = classify_file(f.name)
        try:
            store = db.DBNStore.from_file(f)
            df = store.to_df()
            if df.empty:
                continue
        except Exception as e:
            log.warning(f"  failed to read {f.name}: {e}")
            continue

        if df.index.name == "ts_event":
            df.index.name = "timestamp"
        elif "ts_event" in df.columns:
            df = df.rename(columns={"ts_event": "timestamp"}).set_index("timestamp")

        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")

        parsed = parse_contract_code(file_code)
        month_code = parsed[1] if parsed else "?"

        # Split by instrument_id (each id = one real contract)
        if "instrument_id" in df.columns:
            id_groups = df.groupby("instrument_id")
        else:
            id_groups = [(file_code, df)]

        for iid, sub in id_groups:
            ohlcv_cols = [c for c in ["open", "high", "low", "close", "volume"] if c in sub.columns]
            sub = sub[ohlcv_cols].copy().sort_index()
            sub = sub[~sub.index.duplicated(keep="first")]
            if sub.empty:
                continue

            # Databento prices are 1e9-scale fixed-point integers in some files
            price_cols = [c for c in ["open", "high", "low", "close"] if c in sub.columns]
            if price_cols:
                sample = sub[price_cols[0]].dropna()
                if len(sample) > 0 and sample.iloc[0] > 1_000_000:
                    for col in price_cols:
                        sub[col] = sub[col] / 1e9

            instance_id = f"{file_code}#{iid}"
            instances.append(ContractInstance(
                contract_id=instance_id,
                root=symbol,
                month_code=month_code,
                file_code=file_code,
                first_bar_ts=sub.index.min(),
                last_bar_ts=sub.index.max(),
                df=sub,
            ))

    log.info(f"[{symbol}] loaded {len(instances)} contract instances")
    if instances:
        data_end_ts = max(inst.last_bar_ts for inst in instances)
        for inst in instances:
            inst.expiry_proxy, inst.is_active = compute_expiry_proxy(
                inst.file_code, inst.last_bar_ts, data_end_ts)
        n_active = sum(1 for inst in instances if inst.is_active)
        n_expired = len(instances) - n_active
        log.info(f"  data end: {data_end_ts}")
        log.info(f"  expired contracts: {n_expired}   active (still trading): {n_active}")
        sorted_by_expiry = sorted(instances, key=lambda x: x.expiry_proxy)
        log.info(f"  earliest expiry: {sorted_by_expiry[0].expiry_proxy}  ({sorted_by_expiry[0].file_code})")
        log.info(f"  latest expiry  : {sorted_by_expiry[-1].expiry_proxy}  ({sorted_by_expiry[-1].file_code})")
    return instances


def build_front_month_stitched(instances):
    """Build a front-month continuous series in contiguous blocks.

    Sort contracts by their last_bar_ts (proxy for true expiration). Each
    contract is the front from the previous front's expiration + 1 minute
    until its own expiration. Within that window we take ONLY that
    contract's bars — no per-bar oscillation between contracts.
    """
    if not instances:
        return pd.DataFrame(), []

    sorted_inst = sorted(instances, key=lambda inst: inst.expiry_proxy)

    pieces = []
    used = []
    prev_expiry = None
    skipped = 0

    for inst in sorted_inst:
        own_expiry = inst.expiry_proxy
        if prev_expiry is None:
            window_start = inst.first_bar_ts
        else:
            window_start = prev_expiry + pd.Timedelta(minutes=1)
        window_end = own_expiry

        if window_end < window_start:
            # Already-superseded contract (a later-expiring contract took
            # over before this one's "turn" arrived). Skip without advancing.
            skipped += 1
            continue

        slice_df = inst.df.loc[window_start:window_end].copy()
        if slice_df.empty:
            skipped += 1
            continue

        slice_df["contract_id"] = inst.contract_id
        pieces.append(slice_df)
        used.append(inst)
        prev_expiry = inst.last_bar_ts

    if not pieces:
        return pd.DataFrame(), []

    big = pd.concat(pieces).sort_index()
    big = big[~big.index.duplicated(keep="first")]
    log.info(f"  used {len(used)} contracts as front month, skipped {skipped}")
    return big, used


def compute_roll_spreads(stitched: pd.DataFrame, instances_by_id: dict):
    """Find rolls in the stitched series and compute spread per roll.

    With contiguous blocks, a roll appears exactly when contract_id changes
    from one row to the next. We compute the spread as
        new_contract[old_last_ts].close  -  old_contract[old_last_ts].close
    using each contract's source df, which gives a true overlap-based
    spread free of overnight or session-break gaps.
    """
    rolls = []
    contract_arr = stitched["contract_id"].values
    timestamps = stitched.index

    for i in range(1, len(contract_arr)):
        if contract_arr[i] == contract_arr[i - 1]:
            continue
        old_id = contract_arr[i - 1]
        new_id = contract_arr[i]
        old_inst = instances_by_id.get(old_id)
        new_inst = instances_by_id.get(new_id)
        if old_inst is None or new_inst is None:
            continue

        old_last_ts = timestamps[i - 1]
        old_close = float(old_inst.df.loc[old_last_ts, "close"])

        if old_last_ts in new_inst.df.index:
            new_close = float(new_inst.df.loc[old_last_ts, "close"])
            method = "exact"
        else:
            # asof: last bar of new contract at or before old's last bar
            new_idx = new_inst.df.index
            pos = new_idx.searchsorted(old_last_ts, side="right") - 1
            if pos >= 0:
                new_close = float(new_inst.df["close"].iloc[pos])
                method = "asof"
            else:
                # New contract has no bars before the roll — fall back to
                # its first bar (least accurate; should be very rare).
                new_close = float(new_inst.df["close"].iloc[0])
                method = "first"

        spread = new_close - old_close
        rolls.append((i, timestamps[i], old_id, new_id, spread, method))

    return rolls


def back_adjust(stitched: pd.DataFrame, rolls):
    """Additive Panama back-adjustment.

    Walk backward through the series. At each roll, add the spread to all
    earlier OHLC values so historical prices flow continuously into the
    most recent contract. Volume is never adjusted.
    """
    n = len(stitched)
    bar_adj = [0.0] * n
    roll_index_to_spread = {r[0]: r[4] for r in rolls}

    cumulative = 0.0
    for i in range(n - 1, -1, -1):
        if i in roll_index_to_spread:
            cumulative += roll_index_to_spread[i]
        bar_adj[i] = cumulative

    adj = stitched.copy()
    adj["open"] = adj["open"] + bar_adj
    adj["high"] = adj["high"] + bar_adj
    adj["low"] = adj["low"] + bar_adj
    adj["close"] = adj["close"] + bar_adj
    return adj


def process_symbol(symbol: str):
    log.info(f"\n{'=' * 60}\n  Processing {symbol}\n{'=' * 60}")
    instances = load_symbol_instances(symbol)
    if not instances:
        log.warning(f"  no instances found for {symbol}")
        return

    instances_by_id = {inst.contract_id: inst for inst in instances}

    log.info(f"[{symbol}] building front-month stitched series (contiguous blocks)...")
    stitched, used = build_front_month_stitched(instances)
    if stitched.empty:
        log.warning(f"  empty stitched series for {symbol}")
        return
    log.info(f"[{symbol}] stitched: {len(stitched):,} bars  "
             f"{stitched.index[0]} -> {stitched.index[-1]}")

    log.info(f"[{symbol}] computing roll spreads...")
    rolls = compute_roll_spreads(stitched, instances_by_id)
    log.info(f"[{symbol}] {len(rolls)} rolls detected")
    if rolls:
        spreads = [r[4] for r in rolls]
        log.info(f"  spread stats: min={min(spreads):+.3f}  "
                 f"max={max(spreads):+.3f}  "
                 f"mean={sum(spreads) / len(spreads):+.3f}  "
                 f"abs_sum={sum(abs(s) for s in spreads):.3f}")
        log.info(f"  first 5 rolls:")
        for r in rolls[:5]:
            log.info(f"    {r[1]}  {r[2]:>14} -> {r[3]:<14}  spread={r[4]:+.4f}  ({r[5]})")
        log.info(f"  last 5 rolls:")
        for r in rolls[-5:]:
            log.info(f"    {r[1]}  {r[2]:>14} -> {r[3]:<14}  spread={r[4]:+.4f}  ({r[5]})")

    log.info(f"[{symbol}] applying additive Panama back-adjustment...")
    adjusted = back_adjust(stitched, rolls)

    raw_first = stitched["close"].iloc[0]
    raw_last = stitched["close"].iloc[-1]
    adj_first = adjusted["close"].iloc[0]
    adj_last = adjusted["close"].iloc[-1]
    log.info(f"  raw first close: {raw_first:>10.2f}    raw last close: {raw_last:>10.2f}")
    log.info(f"  adj first close: {adj_first:>10.2f}    adj last close: {adj_last:>10.2f}")
    log.info(f"  cumulative adjustment: {adj_first - raw_first:+.2f}")

    out = adjusted.drop(columns=["contract_id"])
    out_path = OUT_DIR / f"{symbol}_adj.parquet"
    out.to_parquet(out_path, engine="pyarrow")
    size_mb = out_path.stat().st_size / 1_048_576
    log.info(f"  saved -> {out_path}  ({size_mb:.1f} MB)")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for symbol in ["MGC", "MNQ", "MES", "MCL"]:
        try:
            process_symbol(symbol)
        except Exception as e:
            log.error(f"[{symbol}] failed: {e}", exc_info=True)

    log.info("\n" + "=" * 60)
    log.info("DONE — adjusted parquets in data/parquet/*_adj.parquet")
    log.info("Originals at data/parquet/{SYMBOL}.parquet are unchanged.")


if __name__ == "__main__":
    main()
