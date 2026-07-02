"""Strategy entry daemon — fan-out decision cycle + reconnect + startup (reusable engine).

This is the template's orchestration engine — it is strategy-agnostic. It selects the
single configured strategy from the registry and runs it; do NOT put strategy logic here.

The decision cycle uses a 2-phase fan-out:
  Phase 1 (fan-out)   : ThreadPoolExecutor workers fetch market data + evaluate strategy
                        per eligible slot row. Transport is never touched in workers
                        (pika BlockingConnection thread-safety constraint).
  Phase 2 (serial)    : BuySignal results are submitted as buy intents on the main
                        thread in slots[] response order (deterministic).

Cycle elapsed time is measured from eligibility-filter completion to last intent
submission and emitted as `cycle_elapsed_ms` in the result dict. A WARN is logged
when elapsed > poll_interval_seconds.

run_forever catches TransportUnavailableError/RpcTimeoutError and reconnects with
exponential backoff (1s→2s→…→30s cap) instead of exiting.

Startup is fail-closed: RabbitMQ connect, kis-data-hub /health, and Redis ping must
all succeed before the loop starts.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from trex_tvr.market_data.kis_data_hub_client import KisDataHubClient, KisDataHubClientError
from trex_tvr.runtime.config import Config, load_config
from trex_tvr.runtime.intent_builder import build_buy_intent
from trex_tvr.strategies import registry
from trex_tvr.strategies.base import BuySignal, Diagnostic
from trex_tvr.transport.base import EventsPort, IntentPort, RpcTimeoutError, SlotPort, TransportUnavailableError

_KST = timezone(timedelta(hours=9))

logger = logging.getLogger("trex_tvr")


class StartupError(Exception):
    """Raised when the daemon fails a startup gate check (fail-closed)."""


def _now_kst() -> datetime:
    return datetime.now(tz=_KST)


def _compute_backoff(attempt: int, initial: float = 1.0, cap: float = 30.0) -> float:
    """Exponential backoff: initial * 2**attempt, capped at cap seconds."""
    return min(initial * (2.0 ** attempt), cap)


def _eval_slot_work(
    eval_asset: dict,
    slots_resp: dict,
    strategy: Any,
    market_data_client: Any,
    now: datetime,
) -> tuple[dict, Any]:
    """Fan-out worker: fetch market data + evaluate strategy for one slot (Phase 1).

    Returns (eval_asset, decision) where decision is one of:
      BuySignal | HoldDecision | Diagnostic | KisDataHubClientError

    Transport objects are never accessed here — pika BlockingConnection is
    thread-unsafe (architectural invariant).
    """
    code = eval_asset["symbol"]
    try:
        market_data = market_data_client.fetch_market_data(
            code,
            now_kst=now,
            # candle_count is the documented BuyStrategy attr (default 400); getattr
            # keeps duck-typed strategies that omit it working.
            candle_count=getattr(strategy, "candle_count", 400),
        )
    except KisDataHubClientError as exc:
        return eval_asset, exc
    return eval_asset, strategy.evaluate(eval_asset, slots_resp, market_data)


def _ping_redis(redis_url: str) -> None:
    """Startup gate: verify Redis connectivity (fail-closed)."""
    try:
        import redis as _redis
        _redis.Redis.from_url(redis_url).ping()
    except Exception as exc:
        raise StartupError(f"Redis ping failed: {exc}") from exc


# TEMPLATE NOTE: if your strategy benefits from caching per-day candles, wrap the
# market-data client with your own cache and pass it to Daemon(market_data_client=...).
# The base daemon only needs an object exposing
# `fetch_market_data(code, *, now_kst, candle_count)`. Kept out of the engine on purpose.


class Daemon:
    """Orchestrates the decision cycle over injected ports (testable)."""

    def __init__(
        self,
        config: Config,
        *,
        slot_port: SlotPort,
        intent_port: IntentPort,
        events_port: EventsPort,
        market_data_client: Any,
        clock: Callable[[], datetime] = _now_kst,
        reconnect_fn: Optional[Callable[[], None]] = None,
        holiday_cache: Any = None,
    ) -> None:
        self.config = config
        self._slot = slot_port
        self._intent = intent_port
        self._events = events_port
        self._market_data = market_data_client
        self._clock = clock
        self._reconnect_fn = reconnect_fn
        self._holiday_cache = holiday_cache
        # Same-day reentry guard: slot_ids admitted today; reset on new
        # trading day. Working same-day reentry prevention lives here (per-slot),
        # not in the strategy (per_day_limit was removed).
        self._admitted_slots: set[str] = set()
        self._tracker_date: Optional[str] = None
        if config.entry_capital_krw <= 0:
            from trex_tvr.runtime.config import ConfigError
            raise ConfigError(
                f"entry_capital_krw must be a positive integer (got {config.entry_capital_krw!r})"
            )
        self._entry_capital = config.entry_capital_krw
        registry.ensure_default_strategies_loaded()

    @staticmethod
    def _eligible_for_eval(row: dict) -> Optional[str]:
        """Return a diagnostic reason if a slot row is NOT a buy candidate, else None."""
        if row.get("status") != "inactive":
            return "not_inactive"
        if row.get("paused") is True:
            return "paused"
        if row.get("universe_detached") is True:
            return "universe_detached"
        if not row.get("symbol"):
            return "no_symbol"
        return None

    def _slot_for_eval(self, row: dict) -> dict:
        """Map a slot row into the strategy eval asset."""
        return {
            "symbol": row.get("symbol"),
            "slot_id": row.get("slot_id"),
            "entry_capital": self._entry_capital,
        }

    def run_decision_cycle(self, now_kst: Optional[datetime] = None) -> dict:
        now = now_kst or self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=_KST)
        else:
            now = now.astimezone(_KST)

        strategies = registry.get_active_strategies(
            self.config.schedule, now, self.config.strategy_id, self.config.strategy_params
        )
        if not strategies:
            return {"status": "skipped", "reason": "out_of_schedule", "intents": [], "cycle_elapsed_ms": 0}

        # Holiday gate: on a KRX holiday, skip the whole cycle BEFORE the
        # slots RPC so no slots/fan-out work happens on a closed day (trex-core +
        # kis-data-hub load saved). HolidayCache is fail-open — a check
        # failure returns True so the cycle proceeds.
        if self._holiday_cache is not None and not self._holiday_cache.is_trading_day(
            now.strftime("%Y%m%d")
        ):
            return {"status": "skipped", "reason": "holiday", "intents": [], "cycle_elapsed_ms": 0}

        trading_date = now.date().isoformat()
        # Reset the reentry tracker at a new trading day.
        if self._tracker_date != trading_date:
            self._admitted_slots.clear()
            self._tracker_date = trading_date
        slots_resp = self._slot.request_slots(filter="inactive")
        if slots_resp.get("degraded"):
            return {"status": "degraded_hold", "reason": slots_resp.get("reason"), "intents": [], "cycle_elapsed_ms": 0}

        # Pre-flight capacity gate: trex-core's server-side
        # `available_slots` hint == 0 means all slots occupied — every buy intent would
        # be rejected `capacity_full`, so skip the whole cycle and avoid the wasted AMQP
        # round-trips. `None` (max_active_slots unset / older core) means uncapped:
        # pass through to the existing logic. The `capacity_full` admission gate stays
        # the authoritative backstop; this hint never replaces it.
        if slots_resp.get("available_slots") == 0:
            return {
                "status": "capacity_full_hint_skip",
                "reason": "capacity_full_hint_skip",
                "available_slots": 0,
                "intents": [],
                "diagnostics": [{"reason": "capacity_full_hint_skip", "kind": "skipped"}],
                "cycle_elapsed_ms": 0,
            }

        slots = slots_resp.get("slots") or []
        if not slots:
            return {"status": "no_op", "slots": 0, "intents": [], "cycle_elapsed_ms": 0}

        # Eligibility filter — before cycle timer starts.
        diagnostics: list[dict] = []
        eligible: list[tuple[dict, dict]] = []
        for row in slots:
            excluded = self._eligible_for_eval(row)
            if excluded is not None:
                diagnostics.append(
                    {"slot_id": row.get("slot_id"), "reason": excluded, "kind": "excluded"}
                )
                continue
            eval_asset = self._slot_for_eval(row)
            eligible.append((row, eval_asset))

        if not eligible:
            return {"status": "no_op", "slots": len(slots), "intents": [], "diagnostics": diagnostics, "cycle_elapsed_ms": 0}

        # Cycle timer starts after eligibility filter.
        cycle_start = time.monotonic()
        strategy = strategies[0]

        # Phase 1: fan-out — market data fetch + strategy evaluate in parallel workers.
        # Workers MUST NOT access transport (pika thread-safety invariant).
        raw_results: list[tuple[dict, Any]] = []
        with ThreadPoolExecutor(max_workers=self.config.worker_count) as executor:
            futures_map = [
                (eval_asset, executor.submit(_eval_slot_work, eval_asset, slots_resp, strategy, self._market_data, now))
                for _, eval_asset in eligible
            ]
            for eval_asset, fut in futures_map:
                try:
                    _, decision = fut.result()
                    raw_results.append((eval_asset, decision))
                except Exception as exc:
                    raw_results.append(
                        (eval_asset, KisDataHubClientError(str(exc), error_class="other"))
                    )

        # Check for systemic auth failure before Phase 2.
        for eval_asset, decision in raw_results:
            if isinstance(decision, KisDataHubClientError) and decision.error_class == "auth":
                diagnostics.append({"symbol": eval_asset["symbol"], "error_class": "auth"})
                cycle_elapsed_ms = int((time.monotonic() - cycle_start) * 1000)
                return {
                    "status": "aborted",
                    "reason": "kis_auth_failure",
                    "intents": [],
                    "diagnostics": diagnostics,
                    "cycle_elapsed_ms": cycle_elapsed_ms,
                }

        # Phase 2: serial intent submission on the main thread (transport single-owner).
        intents: list[dict] = []
        for eval_asset, decision in raw_results:
            slot_id = eval_asset["slot_id"]
            if isinstance(decision, KisDataHubClientError):
                diagnostics.append({"symbol": eval_asset["symbol"], "error_class": decision.error_class})
                continue
            if isinstance(decision, BuySignal):
                if not slot_id:
                    diagnostics.append(
                        {"symbol": eval_asset["symbol"], "reason": "missing_slot_id", "kind": "diagnostic"}
                    )
                    continue
                # Reentry guard: slot already admitted today -> skip this
                # slot (per-slot). correctness backstop is the identical intent_id
                # dedup at trex-core; this avoids the wasted AMQP round-trip.
                if slot_id in self._admitted_slots:
                    diagnostics.append(
                        {"slot_id": slot_id, "reason": "reentry_skip", "kind": "skipped"}
                    )
                    continue
                payload = build_buy_intent(
                    decision,
                    trading_date=trading_date,
                    slot_id=slot_id,
                    stop_loss_pct=self.config.exit.stop_loss_pct,
                    take_profit_pct=self.config.exit.take_profit_pct,
                    strategy_id=strategy.strategy_id,
                    timeout_sec=self.config.execution.timeout_sec,
                    slippage_pct=self.config.execution.slippage_pct,
                )
                response = self._intent.submit_buy(payload)
                intents.append({"payload": payload, "response": response})
                if response.get("status") == "admitted":
                    self._admitted_slots.add(slot_id)  # reentry guard
                elif response.get("status") == "rejected":
                    diagnostics.append(
                        {"slot_id": slot_id, "reason": response.get("reason"), "kind": "rejected"}
                    )
            elif isinstance(decision, Diagnostic):
                diagnostics.append(
                    {"symbol": eval_asset["symbol"], "reason": decision.reason, "kind": "diagnostic"}
                )

        cycle_elapsed_ms = int((time.monotonic() - cycle_start) * 1000)
        budget_ms = int(self.config.poll_interval_seconds * 1000)
        if cycle_elapsed_ms > budget_ms:
            logger.warning(
                "cycle budget exceeded: elapsed=%dms budget=%dms slots=%d",
                cycle_elapsed_ms, budget_ms, len(eligible),
            )

        observed = self._events.poll_events()
        result = {
            "status": "ok",
            "slots": len(slots),
            "intents": intents,
            "diagnostics": diagnostics,
            "events_observed": len(observed),
            "cycle_elapsed_ms": cycle_elapsed_ms,
        }
        if logger.isEnabledFor(logging.INFO):
            logger.info(
                "cycle ok slots=%d eligible=%d intents=%d diagnostics=%d elapsed=%dms",
                len(slots), len(eligible), len(intents), len(diagnostics), cycle_elapsed_ms,
            )
        return result

    def run_forever(self, _max_cycles: Optional[int] = None) -> None:
        logger.info(
            "started: instance=%s strategy=%s poll=%ss schedule=%s",
            self.config.instance_key,
            self.config.strategy_id,
            self.config.poll_interval_seconds,
            [(s.start, s.end) for s in self.config.schedule],
        )
        backoff_attempt = 0
        cycles_run = 0
        try:
            while _max_cycles is None or cycles_run < _max_cycles:
                try:
                    result = self.run_decision_cycle()
                    cycles_run += 1
                    backoff_attempt = 0
                    logger.info(
                        "cycle status=%s slots=%s intents=%d diagnostics=%d events=%d elapsed=%dms",
                        result.get("status"),
                        result.get("slots"),
                        len(result.get("intents", [])),
                        len(result.get("diagnostics", [])),
                        result.get("events_observed", 0),
                        result.get("cycle_elapsed_ms", 0),
                    )
                    time.sleep(self.config.poll_interval_seconds)
                except (TransportUnavailableError, RpcTimeoutError) as exc:
                    delay = _compute_backoff(backoff_attempt)
                    logger.warning(
                        "transport error (attempt=%d, retry in %.0fs): %s",
                        backoff_attempt + 1, delay, exc,
                    )
                    time.sleep(delay)
                    backoff_attempt += 1
                    if self._reconnect_fn is not None:
                        try:
                            self._reconnect_fn()
                            logger.info("reconnected after %d attempt(s)", backoff_attempt)
                            backoff_attempt = 0
                        except Exception as reconnect_exc:
                            logger.error("reconnect failed: %s", reconnect_exc)
        except KeyboardInterrupt:
            logger.info("shutdown: KeyboardInterrupt")
            return


def build_daemon(config: Config) -> tuple[Daemon, Callable[[], None]]:
    """Wire the real pika + kis-data-hub + Redis dependencies. Fail-closed startup."""
    from trex_tvr.transport.connection import PikaConnection
    from trex_tvr.transport.events_consumer import PikaEventsConsumer
    from trex_tvr.transport.intent_client import PikaIntentClient
    from trex_tvr.transport.slot_client import PikaSlotClient

    registry.ensure_default_strategies_loaded()
    failures = registry.precheck_instantiate_all()
    if failures:
        raise RuntimeError(f"strategy registry precheck failed: {failures}")

    conn = PikaConnection(config.rabbitmq_url)
    kis = KisDataHubClient(
        config.kis_data_hub.base_url,
        config.kis_data_hub.api_key,
        timeout=config.rpc_timeout_seconds,
        allow_stale=config.kis_data_hub.allow_stale,
    )
    try:
        logger.info("startup: connecting to RabbitMQ ...")
        conn.connect()
        logger.info("startup: RabbitMQ connected")
        logger.info("startup: probing kis-data-hub /health (%s) ...", config.kis_data_hub.base_url)
        kis.check_health()
        logger.info("startup: kis-data-hub healthy")
        logger.info("startup: pinging Redis (%s) ...", config.redis_url)
        _ping_redis(config.redis_url)
        logger.info("startup: Redis ping OK")
    except Exception as exc:
        logger.error("startup failed (fail-closed): %s", exc)
        conn.close()
        kis.close()
        raise

    from trex_tvr.market_data.holiday_cache import HolidayCache
    daemon = Daemon(
        config,
        slot_port=PikaSlotClient(conn, config.instance_key, config.rpc_timeout_seconds),
        intent_port=PikaIntentClient(conn, config.instance_key, config.rpc_timeout_seconds),
        events_port=PikaEventsConsumer(conn, config.strategy_id),
        market_data_client=kis,  # plain kis client; wrap with a cache if your strategy needs one
        reconnect_fn=conn.reconnect,
        holiday_cache=HolidayCache(config.redis_url, kis),
    )

    def _shutdown() -> None:
        conn.close()
        kis.close()

    return daemon, _shutdown


def configure_logging(verbose: bool) -> None:
    """Enable INFO-level startup/cycle logging when --verbose is set."""
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger.setLevel(level)
    for noisy in ("pika", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def run_from_config(
    config_path: str, env_file: Optional[str] = None, verbose: bool = False
) -> int:  # pragma: no cover - integration entry.
    configure_logging(verbose)
    config = load_config(config_path, env_file=env_file)
    daemon, shutdown = build_daemon(config)
    try:
        daemon.run_forever()
    finally:
        shutdown()
    return 0


def main(argv: Optional[list[str]] = None) -> int:  # pragma: no cover - thin wrapper.
    from trex_tvr.runtime.cli import main as cli_main

    return cli_main(argv)
