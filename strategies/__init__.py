"""
Strategy package.

A strategy is a subclass of `strategies.base.Strategy`. It is stateless
with respect to position: every open-position detail lives on the
`StrategySlot` the engine passes in, so the same strategy object can run
on several instruments at once, and several strategies can share one
instrument without interfering.

Strategies opt into the registry with the `@register` decorator, which is
what lets the backtest CLI find them by name:

    from strategies import register
    from strategies.base import Strategy

    @register
    class MyStrategy(Strategy):
        name = "MyStrategy"
        ...

    $ python -m backtest.run --strategy MyStrategy --symbol MGC

Nothing here imports a concrete strategy. The engine, the indicators and
the analysis helpers are all strategy-agnostic on purpose — a strategy
can be added or deleted without touching anything else.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from .base import Strategy, EntrySignal, ExitSignal, StrategySlot

REGISTRY: dict[str, type[Strategy]] = {}


def register(cls: type[Strategy]) -> type[Strategy]:
    """Class decorator: make a strategy discoverable by name."""
    key = cls.name
    if key in REGISTRY and REGISTRY[key] is not cls:
        raise ValueError(
            f"Two strategies both call themselves {key!r}: "
            f"{REGISTRY[key].__module__} and {cls.__module__}. "
            f"Give one of them a different `name`."
        )
    REGISTRY[key] = cls
    return cls


def discover() -> dict[str, type[Strategy]]:
    """Import every module in this package so decorators run."""
    for mod in pkgutil.iter_modules([str(Path(__file__).parent)]):
        if not mod.name.startswith("_") and mod.name != "base":
            importlib.import_module(f"{__name__}.{mod.name}")
    return REGISTRY


def get(name: str) -> type[Strategy]:
    """Look up a registered strategy class by name."""
    discover()
    try:
        return REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(REGISTRY)) or "(none registered yet)"
        raise KeyError(f"No strategy named {name!r}. Available: {known}") from None


def available() -> list[str]:
    return sorted(discover())


__all__ = [
    "Strategy", "EntrySignal", "ExitSignal", "StrategySlot",
    "register", "discover", "get", "available", "REGISTRY",
]
