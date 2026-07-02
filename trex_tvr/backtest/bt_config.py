"""Lightweight backtest config loader (strategy-agnostic).

The backtest only needs `[strategy.<id>]` (strategy params, passed straight to the
registry like the live daemon does), `[exit]` (SL/TP), and top-level
`entry_capital_krw`. It deliberately ignores runtime-only sections (schedule,
rabbitmq, kis_data_hub, execution) so a config without them still loads.
"""
from __future__ import annotations

import os
import tomllib
from typing import Any, Optional


def resolve_config_arg(value: str, config_path: Optional[str] = None) -> str:
    """Resolve a `--config` value to a TOML file path.

    An existing file path is used as-is. Otherwise the value is treated as a name
    resolved within `config_path` (default `./config`), mirroring the runtime CLI
    (`--config test` -> `config/test.toml`). Reuses `runtime.config.resolve_config_toml`.
    """
    if os.path.isfile(value):
        return value
    from trex_tvr.runtime.config import resolve_config_toml

    return resolve_config_toml(value, config_path)


def load_backtest_config(
    path: str,
) -> tuple[Optional[str], dict[str, Any], dict[str, Any], Optional[int]]:
    """Read `[strategy.<id>]`, `[exit]`, and top-level `entry_capital_krw` from a TOML.

    Returns `(strategy_id, strategy_params, exit_params, entry_capital_krw)`. The
    strategy params are passed verbatim to `registry.instantiate_strategy` (same
    path as the live daemon), so any strategy's own params work without the backtest
    knowing them. Missing tables yield empty dicts; missing capital yields None.

    Raises `OSError` (unreadable) / `ValueError` (malformed TOML).
    """
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    strategy_id: Optional[str] = None
    strategy_params: dict[str, Any] = {}
    strat_raw = raw.get("strategy")
    if isinstance(strat_raw, dict) and strat_raw:
        strategy_id, params = next(iter(strat_raw.items()))
        if isinstance(params, dict):
            strategy_params = dict(params)

    exit_raw = raw.get("exit")
    exit_params: dict[str, Any] = dict(exit_raw) if isinstance(exit_raw, dict) else {}

    raw_capital = raw.get("entry_capital_krw")
    entry_capital_krw = int(raw_capital) if isinstance(raw_capital, (int, float)) else None

    return (
        str(strategy_id) if strategy_id is not None else None,
        strategy_params,
        exit_params,
        entry_capital_krw,
    )


def pick(flag: Any, cfg: dict[str, Any], key: str, default: Any) -> Any:
    """Resolution precedence: explicit flag > config value > hard-coded default."""
    if flag is not None:
        return flag
    value = cfg.get(key)
    return value if value is not None else default
