"""per-instance TOML (strategy/identity) + env/.env (shared infra & secrets) loader.

Validation is fail-closed: any missing/invalid required key raises `ConfigError`
and the daemon refuses to start.

Config model (12-factor):
  - TOML  : instance_key, schedule, [strategy], [exit], poll/rpc tuning, [cache]
  - env/.env (shared infra + secrets; env wins over TOML when both present):
      TREX_RABBITMQ_URL        -> rabbitmq_url
      KIS_DATA_HUB_BASE_URL    -> kis_data_hub.base_url
      KIS_DATA_HUB_API_KEY     -> kis_data_hub.api_key
      KIS_DATA_HUB_ALLOW_STALE -> kis_data_hub.allow_stale
      TREX_REDIS_URL       -> redis_url

`.env` resolution is deterministic and independent of the current working
directory: an explicit `--env-file` wins, otherwise `<config_dir>/.env` next to
the TOML is used. Real OS environment variables always take precedence over the
`.env` file (`override=False`). python-dotenv missing or no `.env` => no-op.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from typing import Any, Optional

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

DEFAULT_CONFIG_DIR = "config"


def resolve_config_toml(name: str, config_path: Optional[str] = None) -> str:
    """Resolve `--config <name>` within a config directory.

    config_dir = `--config-path` if given, else `./config` (relative to CWD).
    `<name>` selects the TOML inside it; a missing `.toml` extension is appended.
    The matching `.env` is then `<config_dir>/.env` (see `_load_dotenv`).

      resolve_config_toml("test")                 -> config/test.toml
      resolve_config_toml("test.toml")            -> config/test.toml
      resolve_config_toml("prod", "/etc/trex")    -> /etc/trex/prod.toml
    """
    config_dir = config_path or DEFAULT_CONFIG_DIR
    filename = name if name.endswith(".toml") else f"{name}.toml"
    return os.path.join(config_dir, filename)


class ConfigError(Exception):
    """Configuration is missing required keys or has invalid values (fail-closed)."""


@dataclass(frozen=True)
class ScheduleEntry:
    start: str
    end: str


@dataclass(frozen=True)
class KisDataHubConfig:
    base_url: str
    api_key: str = ""
    allow_stale: bool = True


@dataclass(frozen=True)
class ExitConfig:
    stop_loss_pct: float
    take_profit_pct: float


@dataclass(frozen=True)
class ExecutionConfig:
    """Optional REL-006 execution policy. Unset keys are not sent on the
    buy intent, preserving core's default behavior (unbounded limit / no band)."""

    timeout_sec: Optional[int] = None
    slippage_pct: Optional[float] = None


@dataclass(frozen=True)
class Config:
    instance_key: str
    rabbitmq_url: str
    kis_data_hub: KisDataHubConfig
    schedule: list[ScheduleEntry]
    exit: ExitConfig
    strategy_id: str
    entry_capital_krw: int = 0
    strategy_params: dict[str, dict[str, Any]] = field(default_factory=dict)
    execution: ExecutionConfig = ExecutionConfig()
    poll_interval_seconds: float = 30.0
    rpc_timeout_seconds: float = 5.0
    redis_url: str | None = None
    worker_count: int = 20


def _load_dotenv(config_path: str, env_file: Optional[str] = None) -> None:
    """Best-effort load of a `.env` into os.environ.

    Resolution is deterministic and CWD-independent: `env_file` (explicit) wins,
    otherwise `<config_dir>/.env` next to the TOML. Real environment variables
    take precedence over the file (`override=False`). No-op if python-dotenv is
    not installed or the resolved file does not exist.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - optional at runtime
        return
    path = env_file or os.path.join(os.path.dirname(os.path.abspath(config_path)), ".env")
    if os.path.isfile(path):
        load_dotenv(path, override=False)


def _env_bool(name: str) -> Optional[bool]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    val = raw.strip().lower()
    if val in _TRUE:
        return True
    if val in _FALSE:
        return False
    raise ConfigError(f"env {name} must be a boolean (got {raw!r})")


def _require(mapping: dict, key: str, where: str) -> Any:
    if key not in mapping or mapping[key] in (None, ""):
        raise ConfigError(f"missing required config: {where}{key}")
    return mapping[key]


def _parse_schedule(raw: Any) -> list[ScheduleEntry]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("config 'schedule' must be a non-empty array of tables")
    entries: list[ScheduleEntry] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ConfigError(f"schedule[{i}] must be a table")
        start = _require(item, "start", f"schedule[{i}].")
        end = _require(item, "end", f"schedule[{i}].")
        entries.append(ScheduleEntry(start=str(start), end=str(end)))
    return entries


def _parse_execution(raw: Any) -> ExecutionConfig:
    """Parse the optional [execution] table. Both keys are optional; a
    missing table/key means the field is not sent on the buy intent. Negative
    values are rejected fail-closed."""
    if raw is None:
        return ExecutionConfig()
    if not isinstance(raw, dict):
        raise ConfigError("[execution] must be a table")
    timeout_sec: Optional[int] = None
    if raw.get("timeout_sec") is not None:
        try:
            timeout_sec = int(raw["timeout_sec"])
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"execution.timeout_sec must be an int: {exc}") from exc
        if timeout_sec < 0:
            raise ConfigError("execution.timeout_sec must be >= 0")
    slippage_pct: Optional[float] = None
    if raw.get("slippage_pct") is not None:
        try:
            slippage_pct = float(raw["slippage_pct"])
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"execution.slippage_pct must be numeric: {exc}") from exc
        if slippage_pct < 0:
            raise ConfigError("execution.slippage_pct must be >= 0")
    return ExecutionConfig(timeout_sec=timeout_sec, slippage_pct=slippage_pct)


def load_config(path: str, env_file: Optional[str] = None) -> Config:
    """Load + validate config from a TOML path. Raises ConfigError (fail-closed).

    Loads `.env` (config-dir or `--env-file`) into the environment first; real
    env vars still win (override=False).
    """
    _load_dotenv(path, env_file)
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    return _from_mapping(raw)


def _from_mapping(raw: dict) -> Config:
    rabbitmq_url = os.environ.get("TREX_RABBITMQ_URL") or raw.get("rabbitmq_url")
    if not rabbitmq_url:
        raise ConfigError("missing required config: rabbitmq_url (or TREX_RABBITMQ_URL)")

    # [kis_data_hub] TOML table is optional — env may supply everything (12-factor).
    kdh_raw = raw.get("kis_data_hub")
    if kdh_raw is not None and not isinstance(kdh_raw, dict):
        raise ConfigError("[kis_data_hub] must be a table")
    kdh_raw = kdh_raw or {}
    base_url = os.environ.get("KIS_DATA_HUB_BASE_URL") or kdh_raw.get("base_url")
    if not base_url:
        raise ConfigError(
            "missing required config: kis_data_hub.base_url (or KIS_DATA_HUB_BASE_URL)"
        )
    api_key = os.environ.get("KIS_DATA_HUB_API_KEY") or kdh_raw.get("api_key", "")
    allow_stale_env = _env_bool("KIS_DATA_HUB_ALLOW_STALE")
    allow_stale = allow_stale_env if allow_stale_env is not None else bool(kdh_raw.get("allow_stale", True))
    kis = KisDataHubConfig(
        base_url=str(base_url),
        api_key=str(api_key),
        allow_stale=allow_stale,
    )

    schedule = _parse_schedule(raw.get("schedule"))

    exit_raw = raw.get("exit")
    if not isinstance(exit_raw, dict):
        raise ConfigError("missing required config table: [exit]")
    try:
        exit_cfg = ExitConfig(
            stop_loss_pct=float(_require(exit_raw, "stop_loss_pct", "exit.")),
            take_profit_pct=float(_require(exit_raw, "take_profit_pct", "exit.")),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"exit pct values must be numeric: {exc}") from exc

    execution_cfg = _parse_execution(raw.get("execution"))

    # Single-strategy daemon: exactly one [strategy.<id>] table; its key is the
    # strategy_id (also used as the slots strategy scope). No per-window active_ids.
    strat_raw = raw.get("strategy")
    if not isinstance(strat_raw, dict) or len(strat_raw) != 1:
        raise ConfigError("config must define exactly one [strategy.<id>] table")
    strategy_id, sid_params = next(iter(strat_raw.items()))
    if not isinstance(sid_params, dict):
        raise ConfigError(f"[strategy.{strategy_id}] must be a table of parameters")

    cache_raw = raw.get("cache") if isinstance(raw.get("cache"), dict) else {}
    redis_url = os.environ.get("TREX_REDIS_URL") or cache_raw.get("redis_url")
    if not redis_url:
        raise ConfigError(
            "missing required config: redis_url (or TREX_REDIS_URL) — "
            "Redis is required for the holiday cache"
        )

    try:
        worker_count = int(raw.get("worker_count", 20))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"worker_count must be an integer: {exc}") from exc
    if worker_count < 1:
        raise ConfigError("worker_count must be >= 1")

    try:
        entry_capital_krw = int(raw.get("entry_capital_krw", 0))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"entry_capital_krw must be an integer: {exc}") from exc

    return Config(
        instance_key=str(raw.get("instance_key", "default")),
        rabbitmq_url=str(rabbitmq_url),
        kis_data_hub=kis,
        schedule=schedule,
        exit=exit_cfg,
        strategy_id=str(strategy_id),
        entry_capital_krw=entry_capital_krw,
        strategy_params=dict(strat_raw),
        execution=execution_cfg,
        poll_interval_seconds=float(raw.get("poll_interval_seconds", 30.0)),
        rpc_timeout_seconds=float(raw.get("rpc_timeout_seconds", 5.0)),
        redis_url=str(redis_url),
        worker_count=worker_count,
    )
