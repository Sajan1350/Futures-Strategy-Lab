"""
Probe the ProjectX /History/retrieveBars endpoint to find out:
  1. What max `limit` the gateway actually honours per request
  2. How far back 1m history is available per instrument
  3. How long a full backfill would take (rate-limit friendly)

Run:
    python -m scripts.probe_history

Nothing is written to disk. Read-only probe.
"""

import logging
import time
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

from broker.projectx_client import ProjectXClient, UNIT_MINUTE

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("probe")

SYMBOLS = ["MGC", "MNQ", "MES", "MCL"]
LIMIT_TESTS = [500, 5_000, 20_000, 50_000]
WALKBACK_CHUNK_DAYS = 30     # window size when walking backward
WALKBACK_MAX_YEARS  = 10     # stop after this far back


def _bar_time(bar):
    return bar.get("t") or bar.get("timestamp") or bar.get("time")


def probe_limit(client, contract_id, symbol):
    """Find the largest limit the gateway actually returns for a 1-year window."""
    log.info(f"=== {symbol}: probing max limit per request ===")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365)
    best = 0
    for lim in LIMIT_TESTS:
        try:
            t0 = time.time()
            bars = client.get_bars(contract_id, UNIT_MINUTE, 1, start, end_time=end, limit=lim)
            dt = time.time() - t0
            n = len(bars)
            log.info(f"  limit={lim:>6}  returned={n:>6}  elapsed={dt:.2f}s")
            best = max(best, n)
            if n < lim:
                # gateway gave us fewer than we asked — that's the true ceiling
                break
        except Exception as e:
            log.warning(f"  limit={lim} FAILED: {e}")
            break
    return best


def probe_walkback(client, contract_id, symbol, per_request):
    """Walk startTime backward in chunks until the gateway returns nothing."""
    log.info(f"=== {symbol}: walking history backward (chunk={WALKBACK_CHUNK_DAYS}d) ===")
    end = datetime.now(timezone.utc)
    oldest_seen = None
    total = 0
    chunks = 0
    max_chunks = int((365 * WALKBACK_MAX_YEARS) / WALKBACK_CHUNK_DAYS)
    for _ in range(max_chunks):
        start = end - timedelta(days=WALKBACK_CHUNK_DAYS)
        try:
            bars = client.get_bars(
                contract_id, UNIT_MINUTE, 1, start,
                end_time=end, limit=max(per_request, 50_000),
            )
        except Exception as e:
            log.warning(f"  chunk {start.date()} → {end.date()} FAILED: {e}")
            break
        if not bars:
            log.info(f"  chunk {start.date()} → {end.date()} returned 0 — stopping walkback")
            break
        n = len(bars)
        total += n
        chunks += 1
        first_t = _bar_time(bars[0])
        last_t  = _bar_time(bars[-1])
        oldest_seen = first_t
        log.info(f"  chunk {start.date()} → {end.date()}  bars={n:>6}  first={first_t}  last={last_t}")
        end = start
        time.sleep(0.25)  # be nice to the gateway
    log.info(f"  TOTAL for {symbol}: chunks={chunks}  bars={total}  oldest={oldest_seen}")
    return total, oldest_seen


def main():
    client = ProjectXClient()
    client.login()

    summary = {}
    for sym in SYMBOLS:
        try:
            contract = client.get_front_month_contract(sym)
        except Exception as e:
            log.error(f"{sym}: could not resolve contract — {e}")
            continue
        cid = contract.get("id")
        log.info(f"\n######## {sym}  contract={contract.get('name')}  id={cid} ########")

        per_req = probe_limit(client, cid, sym)
        if per_req == 0:
            log.warning(f"{sym}: gateway returned no bars at any limit — skipping walkback")
            summary[sym] = (0, 0, None)
            continue

        total, oldest = probe_walkback(client, cid, sym, per_req)
        summary[sym] = (per_req, total, oldest)

    log.info("\n================ SUMMARY ================")
    log.info(f"{'symbol':<6} {'max/req':>10} {'total bars':>12} {'oldest bar':>22}")
    for sym, (per_req, total, oldest) in summary.items():
        log.info(f"{sym:<6} {per_req:>10} {total:>12} {str(oldest):>22}")


if __name__ == "__main__":
    main()
