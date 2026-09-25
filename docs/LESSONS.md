# Lessons from the previous codebase

The ATR Trend Follower and the momentum scalper that preceded it were
scrapped in September 2026. Their code is gone; what follows is the part
worth keeping — findings that cost real backtest cycles to establish.

These are observations from **one instrument set** (MGC, MNQ, MES, MCL),
**one timeframe** (5-min), and **one exit model**. They are starting
hypotheses for the next strategy, not laws.

---

## Filters that were tried and removed

**MACD (8,17,9) entry gate** — cut ~820 trades but lowered both win rate
and profit factor. It was redundant with a 9/20 EMA state check already in
the entry stack: two indicators expressing the same thing, so the second
one only removed trades at random. *Before adding an indicator, ask what
it measures that the existing stack doesn't.*

**9-EMA slope minimum** — requiring the fast EMA to be rising by a minimum
percentage over 20 bars dropped PF from 0.11 to 0.08. It filtered out
entries taken on pullbacks, which were disproportionately the profitable
ones. *A "stronger trend" filter can select for late entries.*

**Looser trailing stops** — a sweep across five trail modes (ratchet to
inner band, buffered band, EMA basis, ATR-from-extreme, none) found the
tightest option best; the looser variants cost 26–40% of PF despite
graduating more trades to the far target. The trail-stop exit stream, not
the runner, was where the money came from.

---

## Findings that held up

**Time of day dominated everything else.** Per-symbol hour-of-day
filtering was the single largest improvement ever measured on that
strategy: PF 0.66 → 0.86, and roughly $560K of simulated loss removed.
Critically, a *universal* session restriction was worse than per-symbol
hours — each instrument had its own good and bad hours. If nothing else
carries over, check hour-of-day early.

**Marginal volatility expansion was the worst entry bucket.** Splitting
entries into quintiles by `atr_fast / atr_slow`, the weakest bucket
(1.00–1.11) was the largest single loss source (PF 0.78, avg −$91/trade).
Requiring "some expansion" is not the same as requiring enough.

**Non-monotonic features exist and matter.** Normalised EMA spread at
entry was profitable in the middle of its range and lossy at *both* ends —
too narrow meant no move yet, too wide meant the move was over. A simple
`> threshold` filter would have captured none of this. Bucket a feature
before assuming its relationship to PnL is a straight line; the bucketing
helpers in `backtest/analysis/buckets.py` exist for exactly this.

**Fill price assumptions changed conclusions.** Booking target exits at
the bar close instead of the band price understated the average winner by
a factor of four ($16.73 → $69.09). Exit modelling deserves the same
scrutiny as entry logic.

---

## The mistake to avoid repeating

Every filter threshold on that strategy — the allowed trading hours, the
per-symbol spread bands, the expansion minimum — was fitted on the full
history and then "validated" on that same history. The headline result
(PF 1.38, +$113K) was therefore in-sample, and the out-of-sample re-test
was still pending when work stopped. There is no way to know now how much
of that edge was real.

**Hold back a test period before fitting anything.** Decide the split
first, fit on the earlier portion only, and touch the held-out period once.

A related symptom: the final configuration produced ~1,118 entries across
four symbols over roughly seven years — about one trade every other day,
and one symbol (MNQ) was starved to 22 trades total. Filters were stacked
until almost nothing survived. A filter that removes 69% of trades needs
to justify itself on out-of-sample data, not just improve an in-sample
ratio. Track trade count as a first-class constraint, not an afterthought.
