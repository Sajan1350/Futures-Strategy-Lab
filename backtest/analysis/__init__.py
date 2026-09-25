"""Strategy-agnostic post-backtest analysis helpers."""
from .buckets import (
    aggregate_entries,
    join_entry_features,
    bucket_stats,
    quantile_report,
    category_report,
    print_table,
)

__all__ = [
    "aggregate_entries", "join_entry_features", "bucket_stats",
    "quantile_report", "category_report", "print_table",
]
