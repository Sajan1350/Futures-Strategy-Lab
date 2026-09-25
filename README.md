# Futures Strategy Lab

A research and execution platform for intraday futures strategies on
MGC, MNQ, MES and MCL. The platform is deliberately **strategy-free**:
data handling, indicators, the backtest engine, risk controls and broker
plumbing all live here, and strategies plug into them.

There are currently **no strategies** in this repo — only a template. That
is intentional. See [docs/LESSONS.md](docs/LESSONS.md) for what the
previous generation of strategies taught before being retired.

---

## Quickstart

```bash
python -m core.indicator_catalog            # what primitives are available
python -m backtest.run --list               # what strategies are registered
python -m backtest.run --strategy Template --symbol MGC \
                       --start 2025-01-01 --end 2025-06-30
```

The `Template` strategy has no edge and is not meant to be traded or
tuned. It exists so the whole pipeline can be exercised in one command.

## Layout

```
config.py             Platform parameters ONLY - indicator lengths,
                      session window, risk limits. No strategy params.
instruments.py        Per-instrument tick size, point value, session, limits

core/
  indicators.py       Indicator columns (EMA, ATR, Keltner, VWAP, ...)
  runtime.py          Session mask, PT hour, cross freshness; optional bias
  risk.py             Daily and consecutive-loss kill switches
  indicator_catalog.py  Browsable registry of every primitive -> INDICATORS.md

strategies/
  base.py             Strategy ABC + EntrySignal / ExitSignal / StrategySlot
  template.py         Documented skeleton. Copy this to start.
  __init__.py         Name registry (@register)

backtest/
  data.py             Parquet loaders + 5m / 1h / daily resamplers
  engine.py           Strategy-agnostic bar loop, partial exits, risk
  broker_sim.py       Fills, slippage, commission
  metrics.py          PF, win rate, drawdown, Sharpe/Sortino, expectancy
  analysis/buckets.py Quantile / category performance breakdowns
  run.py              CLI

broker/               ProjectX auth, market data, realtime aggregation
scripts/              Data pipeline + account helpers
data/                 Market data (gitignored - see Data below)
```

## The one structural rule

**`config.py` holds platform parameters. Strategy parameters live in the
strategy module.**

The previous codebase put both in `config.py`. The result was that one
strategy's fitted per-symbol thresholds became global state that every
other strategy silently inherited, and no strategy could be read or
deleted as a single unit. Entry thresholds, stop multiples, target
placement and sizing rules belong next to the rules that consume them.

Likewise, the indicator catalog lists indicators, derived features and
risk machinery — but not entry filters. A filter is a rule a strategy
applies, not a primitive the platform provides.

## Writing a strategy

1. `python -m core.indicator_catalog` to see what is already computed.
2. Copy `strategies/template.py`, rename the class and its `name`.
3. Implement `evaluate_entry`, `evaluate_exit`, optionally `update_trail`.
   Keep the strategy's parameters at the top of that module.
4. `python -m backtest.run --strategy YourName --symbol MGC`

The engine calls `evaluate_entry` only when flat and `evaluate_exit` only
when in a position, checks the stop itself before asking for an exit, and
supports partial exits (return `ExitSignal(size=n)` with `n < slot.size`).
Always respect `ctx["can_enter"]` — it carries the kill-switch state.

### Before you fit anything

Decide the out-of-sample split first and fit only on the earlier portion.
The previous strategy's headline result was fitted and validated on the
same history and never survived a real out-of-sample test. Also watch
trade count as you stack filters — see [docs/LESSONS.md](docs/LESSONS.md).

## Data

1-minute OHLCV from Databento (GLBX.MDP3), 2010-06-06 → 2026-04-06.

```
data/raw/                 1,437 .dbn.zst per-contract files (238 MB)
data/parquet/{SYM}.parquet      front-month splice, not price-adjusted
data/parquet/{SYM}_adj.parquet  Panama back-adjusted continuous
```

Both directories are gitignored — this repo does not carry the data.

Rebuild from raw:

```bash
python -m scripts.convert_databento     # -> {SYM}.parquet
python -m scripts.back_adjust           # -> {SYM}_adj.parquet
```

Pass `--adjusted` to the backtest CLI to use the back-adjusted series.

**Backup:** `OneDrive/Sajan - Trading/Futures Data Backup 2026-09-25/`
(the raw Databento archive plus all eight parquets, md5-verified).

## Live trading

`broker/` holds ProjectX authentication, historical bar fetching and the
realtime aggregator, all carried over and working. `scripts/list_accounts.py`
prints the visible accounts with their PAPER/LIVE classification — run it
before any live session.

The previous live loop and dashboard were written against the retired
strategy and were not carried over. They will need rebuilding on top of
whatever strategy comes next.
