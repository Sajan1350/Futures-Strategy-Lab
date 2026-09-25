"""
Inspect ONE Databento .dbn.zst file to discover what symbol / instrument_id
information is available in this SDK version.

Run:
    python -m scripts.inspect_dbn

Prints:
    - File chosen
    - DBNStore attributes related to symbology
    - to_df() columns + dtypes
    - Unique instrument_ids in the file
    - Sample symbol names if mappable
    - Date ranges per instrument_id (so we can see whether databento packs
      multi-decade contracts into one file)
"""

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT / "data" / "raw"


def main():
    try:
        import databento as db
    except ImportError:
        print("databento not installed. Run: pip install databento")
        sys.exit(1)

    # Pick the first MGCG file we can find — small enough but multi-instance
    candidates = sorted(RAW_DIR.glob("*.MGCG0.dbn.zst")) \
                 or sorted(RAW_DIR.glob("*.MGCG*.dbn.zst")) \
                 or sorted(RAW_DIR.glob("*.MGC*.dbn.zst"))
    if not candidates:
        print("No MGC files found in data/raw/")
        sys.exit(1)
    f = candidates[0]
    print(f"Inspecting: {f.name}")
    print("-" * 70)

    store = db.DBNStore.from_file(f)
    print(f"DBNStore type: {type(store).__name__}")
    print(f"Databento SDK version: {db.__version__}")
    print()

    # Symbology-related attrs
    print("SYMBOLOGY-RELATED ATTRIBUTES on DBNStore:")
    attrs = [a for a in dir(store) if not a.startswith("_")
             and ("sym" in a.lower() or "instrum" in a.lower()
                  or "metadata" in a.lower() or "request" in a.lower())]
    for a in attrs:
        try:
            val = getattr(store, a)
            kind = type(val).__name__
            # Truncate large repr
            preview = repr(val)
            if len(preview) > 200:
                preview = preview[:200] + "..."
            print(f"  store.{a}  ({kind}) = {preview}")
        except Exception as e:
            print(f"  store.{a}  -> ERROR: {e}")
    print()

    # Try to_df with default args
    print("to_df() — default args:")
    try:
        df = store.to_df()
        print(f"  shape: {df.shape}")
        print(f"  index name: {df.index.name}")
        print(f"  columns: {list(df.columns)}")
        print(f"  dtypes:\n{df.dtypes.to_string()}")
        if "instrument_id" in df.columns:
            unique_ids = df["instrument_id"].unique()
            print(f"\n  unique instrument_ids ({len(unique_ids)}): {list(unique_ids)[:20]}")
            for iid in list(unique_ids)[:10]:
                sub = df[df["instrument_id"] == iid]
                print(f"    id={iid}: {len(sub):,} bars  "
                      f"{sub.index.min()} -> {sub.index.max()}")
        if "symbol" in df.columns:
            unique_syms = df["symbol"].unique()
            print(f"\n  unique symbols ({len(unique_syms)}): {list(unique_syms)[:20]}")
        print(f"\n  first 3 rows:\n{df.head(3).to_string()}")
    except Exception as e:
        print(f"  ERROR: {e}")
    print()

    # Try to_df with symbol mapping flags
    print("to_df(pretty_ts=True, map_symbols=True):")
    try:
        df2 = store.to_df(pretty_ts=True, map_symbols=True)
        print(f"  shape: {df2.shape}")
        print(f"  columns: {list(df2.columns)}")
        if "symbol" in df2.columns:
            unique_syms = df2["symbol"].unique()
            print(f"  unique symbols ({len(unique_syms)}): {list(unique_syms)[:20]}")
            for sym in list(unique_syms)[:10]:
                sub = df2[df2["symbol"] == sym]
                print(f"    {sym}: {len(sub):,} bars  "
                      f"{sub.index.min()} -> {sub.index.max()}")
    except Exception as e:
        print(f"  ERROR: {e}")
    print()

    # Try symbology_map directly
    print("store.symbology_map (if exists):")
    try:
        sm = getattr(store, "symbology_map", None)
        if sm is None:
            print("  attribute not present")
        else:
            print(f"  type: {type(sm).__name__}")
            if hasattr(sm, "items"):
                items = list(sm.items())[:20]
                print(f"  first 20 items: {items}")
            else:
                print(f"  preview: {repr(sm)[:300]}")
    except Exception as e:
        print(f"  ERROR: {e}")


if __name__ == "__main__":
    main()
