# Data quality audit — 2026-09-26

Audit of `data/parquet/` before building any strategy on it. Summary
first, then the evidence.

**Verdict:** the Panama-adjusted series (`{SYM}_adj.parquet`) is
structurally sound and usable. The raw series (`{SYM}.parquet`) is
corrupt and must not be used. For MGC the practical backtest window is
**2022-01-01 → 2026-04-06**, restricted to gold's active hours.

---

## 1. The raw series is corrupt — do not use it

`scripts/convert_databento.py` merged every contract month onto one
timeline instead of splicing the front month. Six consecutive MGC
minutes on 2025-06-02:

| time (UTC) | close | volume | |
|---|---|---|---|
| 14:30 | 3424.8 | 2 | back month |
| 14:31 | 3399.5 | 282 | **front month** |
| 14:33 | 3426.3 | 5 | back month |
| 14:35 | 3455.0 | 2 | further out |

Median absolute 1-minute change, 2025:

| | raw | adjusted | inflation |
|---|---|---|---|
| MGC | 13.500 | 0.600 | **22×** |
| MNQ | 5.750 | 2.750 | 2× |
| MES | 1.000 | 0.500 | 2× |
| MCL | 0.050 | 0.020 | 2.5× |

`adjusted=False` used to be the default, so every backtest run before
this audit used the corrupt series. The default is now `True` and the
opt-in flag is `--raw`. `convert_databento.py` has **not** been fixed;
the raw files are kept only so a corrected splice can be compared.

## 2. Structural integrity of `MGC_adj` — clean

1,769,555 1-minute bars, 2010-10-03 → 2026-04-06.

| check | result |
|---|---|
| high < low | 0 |
| high < max(open, close) | 0 |
| low > min(open, close) | 0 |
| non-positive prices | 0 |
| NaN rows | 0 |
| duplicate timestamps | 0 |
| out-of-order index | none |
| zero-volume bars | 0 |

## 3. Large moves are gaps, not bad ticks

Of 112 one-minute moves greater than 50 points, only **13** occur
between genuinely consecutive minutes:

| preceding gap | count |
|---|---|
| 1 min (real tick) | 13 |
| 2–5 min | 2 |
| 5–60 min | 23 |
| > 1 hour | 74 |

Eight of the thirteen real ones are in January 2026, during an actual
gold melt-up and crash. The data is representing a violent market, not
malfunctioning. But a backtest must not treat a post-gap jump as a
tradeable move — there was no continuous price to trade through.

## 4. Coverage — the real constraint

Minutes present per day in the 06:00–13:00 ET window (420 expected):

| year | days | median min/day | coverage |
|---|---|---|---|
| 2010–2016 | ~200/yr | 34–197 | 8–47% |
| 2017 | 207 | 266 | 63% |
| 2018 | 200 | 304 | 72% |
| 2019 | 197 | 357 | 85% |
| 2020 | 224 | 395 | 94% |
| 2021 | 234 | 299 | 71% |
| 2022 | 214 | 416 | 99% |
| 2023 | 224 | 419 | 100% |
| 2024 | 227 | 414 | 99% |
| 2025 | 239 | 399 | 95% |
| 2026 | 63 | 420 | 100% |

MGC launched in late 2010 and was thinly traded for years. **Anything
before 2022 is too sparse to backtest on**, and 2021 dips for reasons
not yet understood.

Even inside the good years, MGC is patchier than the index products.
5-minute bars per day, 2023-01-01 onward (276 = a full day):

| | MGC | MNQ |
|---|---|---|
| days with any data | 957 | 1,017 |
| median bars/day | 129 (47%) | 276 (100%) |
| days ≥90% complete | 34% | 63% |
| days with ≥34 of 36 bars in 05:00–07:00 PT | **66%** | 100% |

The gaps are spread evenly across the clock rather than concentrated
overnight. Gold's active window is the best-covered part of the day, at
707 usable sessions over the period.

Two consequences:

* **Trade the active window.** It is both where the data is good and
  where gold actually moves.
* **Bar-counting features are unreliable.** `bars_since_bull_cross` and
  anything like it measures bars, not time, and a bar is not a fixed
  amount of time when half the day is missing. Prefer elapsed time, or
  gate entries on a minimum bar count for the session.

## 5. Volatility regime has shifted hard

Active-session median absolute 1-minute move:

| year | median \|1m\| | p99 | median price | move in bp |
|---|---|---|---|---|
| 2018 | 0.200 | 1.40 | 1614.5 | 1.24 |
| 2021 | 0.300 | 2.90 | 2064.0 | 1.45 |
| 2023 | 0.300 | 2.60 | 2189.4 | 1.37 |
| 2025 | 0.600 | 5.70 | 3430.2 | 1.75 |
| 2026 | **1.400** | **16.20** | 4886.1 | **2.87** |

Gold's typical 1-minute move is roughly **5× larger in absolute points
and 2× larger in relative terms** than in 2023. Any rule expressed in
fixed points and fitted on older data will be badly mis-scaled today.
**Express every distance in ATR units.**

## 6. What to do

* Backtest window: **2022-01-01 → 2026-04-06**.
* Restrict entries to gold's active hours.
* Require a minimum number of bars in the session before trading, so
  sparse days are skipped rather than traded on partial information.
* Normalise all distances by ATR.
* **2026-04-06 → today is untouched by any fitting** and is reserved as
  a forward test. Do not look at it while developing.
