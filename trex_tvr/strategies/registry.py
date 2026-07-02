"""BuyStrategy registry + time-window schedule resolver.

Ported from trex-orbit `app/strategies/buy/registry.py`. Registry signature:
  `_BUY_STRATEGIES: dict[str, tuple[type[BuyStrategy], dict[str, Any]]]`

The same class may be registered under multiple strategy_ids with different
parameter sets. `get_active_strategies(schedule, now_kst, strategy_params)`
returns instances for the schedule entry whose `[start, end)` window contains
`now_kst`; empty list when out-of-schedule. `strategy_params` (from config
`[strategy.<id>]`) overrides the registry's default kwargs per instance.

This module imports nothing from transport/market_data/redis/pika/httpx.
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Any

from trex_tvr.strategies.base import BuyStrategy
from trex_tvr.strategies.example_strategy import TvrStrategy

# strategy_id -> (class, default_kwargs)
_BUY_STRATEGIES: dict[str, tuple[type[BuyStrategy], dict[str, Any]]] = {}


def _parse_hhmm(value: str) -> time:
    """Parse `"HH:MM"` (KST, 24-hour). `"24:00"` maps to end-of-day."""
    if value == "24:00":
        return time(23, 59, 59, 999999)
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def _entry_get(entry: object, key: str, default: Any = None) -> Any:
    """Read `key` from a schedule entry (dataclass attr or dict key)."""
    value = getattr(entry, key, None)
    if value is None and isinstance(entry, dict):
        value = entry.get(key, default)
    return default if value is None else value


def _entry_active_at(entry: object, now_kst: datetime) -> bool:
    """Return True if `now_kst` falls within entry's `[start, end)` window."""
    start_str = _entry_get(entry, "start")
    end_str = _entry_get(entry, "end")
    if start_str is None or end_str is None:
        return False
    now_t = now_kst.time()
    return _parse_hhmm(start_str) <= now_t < _parse_hhmm(end_str)


def register(
    strategy_id: str, cls: type[BuyStrategy], kwargs: dict[str, Any] | None = None
) -> None:
    """Register `(cls, kwargs)` under `strategy_id`. Overwrites existing entry."""
    _BUY_STRATEGIES[strategy_id] = (cls, dict(kwargs or {}))


def is_registered(strategy_id: str) -> bool:
    return strategy_id in _BUY_STRATEGIES


def registered_ids() -> list[str]:
    return sorted(_BUY_STRATEGIES)


def instantiate_strategy(
    strategy_id: str, override_kwargs: dict[str, Any] | None = None
) -> BuyStrategy:
    """Instantiate the strategy at `strategy_id`, merging registry default kwargs
    with `override_kwargs` (config `[strategy.<id>]`). Sets the instance
    `strategy_id` attribute to the registry key.
    """
    if strategy_id not in _BUY_STRATEGIES:
        raise KeyError(
            f"buy strategy_id={strategy_id!r} not registered; available: {registered_ids()}"
        )
    cls, kwargs = _BUY_STRATEGIES[strategy_id]
    merged = {**kwargs, **(override_kwargs or {})}
    instance = cls(**merged)
    instance.strategy_id = strategy_id
    return instance


def get_active_strategies(
    schedule: list[object] | None,
    now_kst: datetime,
    strategy_id: str,
    strategy_params: dict[str, dict[str, Any]] | None = None,
) -> list[BuyStrategy]:
    """Return the active BuyStrategy when `now_kst` is inside a schedule window.

    Single-strategy daemon: schedule entries only gate by time (`[start, end)`);
    the one `strategy_id` runs in every window. Returns `[strategy]` when active,
    `[]` outside all windows. `strategy_params` maps strategy_id -> kwargs override
    (from config `[strategy.<id>]`).
    """
    params = strategy_params or {}
    for entry in schedule or []:
        if _entry_active_at(entry, now_kst):
            return [instantiate_strategy(strategy_id, params.get(strategy_id))]
    return []


def precheck_instantiate_all() -> list[tuple[str, str]]:
    """Startup precheck: instantiate every registered entry. Returns (id, error)
    for failures; empty on success.
    """
    failures: list[tuple[str, str]] = []
    for strategy_id in registered_ids():
        try:
            instantiate_strategy(strategy_id)
        except Exception as exc:  # noqa: BLE001 — collect all failures.
            failures.append((strategy_id, f"{type(exc).__name__}: {exc}"))
    return failures


def ensure_default_strategies_loaded() -> None:
    """Register the BuyStrategy entries this daemon serves. Idempotent.

    strategy_id MUST match the single `[strategy.<id>]` table in the instance
    TOML (it also drives the snapshot strategy_key and the events queue name).
    """
    register("tvr_v1", TvrStrategy, {})
