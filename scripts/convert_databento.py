"""
Convert Databento .dbn.zst files to clean per-instrument parquet files.

Reads all .dbn.zst files from data/raw/, groups by instrument (MGC, MNQ, MES, MCL),
produces one parquet per instrument at data/parquet/{SYMBOL}.parquet with columns:
    timestamp (UTC datetime), open, high, low, close, volume

Run:
    python -m scripts.convert_databento
"""

import os
import sys
import zipfile
import logging
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("convert")

PROJECT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT / "data" / "raw"
OUT_DIR = PROJECT / "data" / "parquet"
ZIP_PATH = Path.home() / "Downloads" / "GLBX-20260410-UJQ8CTBEMG.zip"

# Map Databento instrument root symbols to our canonical names
SYMBOL_MAP = {
    "MGC": "MGC",
    "MNQ": "MNQ",
    "MES": "MES",
    "MCL": "MCL",
    "MCLF": "MCL",   # MCL contract roots sometimes appear as MCLF/MCLG etc.
    "MCLE": "MCL",
}

def classify_file(filename: str) -> str | None:
    """Extract the canonical instrument name from a .dbn.zst filename.

    IMPORTANT: Skip calendar-spread files.  Spreads have TWO contract
    identifiers separated by a hyphen after the schema part, e.g.
        ...ohlcv-1m.MCLF2-MCLG2.dbn.zst   ← SPREAD (skip)
        ...ohlcv-1m.MCLF2.dbn.zst          ← individual (keep)

    We detect spreads by checking the segment between 'ohlcv-1m.' and
    '.dbn' for a hyphen.
    """
    name_lower = filename.lower()

    # Extract the instrument segment: everything between 'ohlcv-1m.' and '.dbn'
    marker = "ohlcv-1m."
    idx_start = name_lower.find(marker)
    if idx_start < 0:
        return None
    idx_start += len(marker)
    idx_end = name_lower.find(".dbn", idx_start)
    if idx_end < 0:
        return None
    instrument_seg = filename[idx_start:idx_end]  # e.g. "MCLF2" or "MCLF2-MCLG2"

    # Skip spreads (contain a hyphen → two legs)
    if "-" in instrument_seg:
        return None

    name = filename.upper()
    for root, canonical in sorted(SYMBOL_MAP.items(), key=lambda x: -len(x[0])):
        if root in name:
            return canonical
    return None


def step1_unzip():
    """Unzip the Databento download into data/raw/."""
    if not ZIP_PATH.exists():
        log.error(f"Zip not found at {ZIP_PATH}")
        sys.exit(1)

    existing = list(RAW_DIR.glob("*.dbn.zst"))
    if existing:
        log.info(f"data/raw/ already has {len(existing)} .dbn.zst files — skipping unzip")
        return

    log.info(f"Unzipping {ZIP_PATH} → {RAW_DIR} ...")
    with zipfile.ZipFile(ZIP_PATH, "r") as zf:
        zf.extractall(RAW_DIR)
    count = len(list(RAW_DIR.rglob("*.dbn.zst")))
    log.info(f"Extracted {count} .dbn.zst files + condition.json")


def step2_convert():
    """Read all .dbn.zst files per instrument and write clean parquet."""
    try:
        import databento as db
    except ImportError:
        log.error("databento not installed. Run: pip install databento")
        sys.exit(1)

    # Collect files per instrument
    files_by_sym: dict[str, list[Path]] = {}
    for f in sorted(RAW_DIR.rglob("*.dbn.zst")):
        sym = classify_file(f.name)
        if sym:
            files_by_sym.setdefault(sym, []).append(f)
        else:
            log.warning(f"Skipping unrecognised file: {f.name}")

    log.info(f"Found instruments: {list(files_by_sym.keys())}")

    for sym, paths in sorted(files_by_sym.items()):
        log.info(f"\n{'='*60}")
        log.info(f"Processing {sym}: {len(paths)} files")
        dfs = []
        for p in paths:
            try:
                store = db.DBNStore.from_file(p)
                df = store.to_df()
                if df.empty:
                    continue
                dfs.append(df)
            except Exception as e:
                log.warning(f"  Failed to read {p.name}: {e}")
                continue

        if not dfs:
            log.warning(f"  No data for {sym} — skipping")
            continue

        combined = pd.concat(dfs, ignore_index=False)

        # Databento OHLCV-1m schema columns vary slightly by version.
        # Common: ts_event (or index), open, high, low, close, volume
        # Normalise to our standard columns.
        if "ts_event" in combined.columns:
            combined = combined.rename(columns={"ts_event": "timestamp"})
            combined = combined.set_index("timestamp")
        elif combined.index.name == "ts_event":
            combined.index.name = "timestamp"

        # Keep only OHLCV columns
        ohlcv_cols = []
        for col in ["open", "high", "low", "close", "volume"]:
            if col in combined.columns:
                ohlcv_cols.append(col)
        combined = combined[ohlcv_cols]

        # Databento prices are in fixed-point (multiplied by 1e9). Convert.
        price_cols = [c for c in ["open", "high", "low", "close"] if c in combined.columns]
        sample_val = combined[price_cols[0]].dropna().iloc[0] if len(combined) > 0 else 0
        if sample_val > 1_000_000:
            # Values are in fixed-point notation (1e9 scale)
            log.info(f"  Detected fixed-point prices (sample={sample_val:.0f}), dividing by 1e9")
            for col in price_cols:
                combined[col] = combined[col] / 1e9

        # Sort by timestamp, drop exact duplicates
        combined = combined.sort_index()
        before = len(combined)
        combined = combined[~combined.index.duplicated(keep="first")]
        after = len(combined)
        if before != after:
            log.info(f"  Removed {before - after} duplicate timestamps")

        # Ensure UTC timezone
        if combined.index.tz is None:
            combined.index = combined.index.tz_localize("UTC")

        out_path = OUT_DIR / f"{sym}.parquet"
        combined.to_parquet(out_path, engine="pyarrow")

        first = combined.index[0]
        last = combined.index[-1]
        log.info(f"  {sym}: {len(combined):,} bars  |  {first} → {last}")
        log.info(f"  Saved to {out_path}")
        log.info(f"  Price range: {combined['close'].min():.2f} – {combined['close'].max():.2f}")


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    step1_unzip()
    step2_convert()

    log.info("\n" + "=" * 60)
    log.info("DONE — Parquet files ready in data/parquet/")
    for f in sorted(OUT_DIR.glob("*.parquet")):
        size_mb = f.stat().st_size / 1_048_576
        log.info(f"  {f.name}: {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
