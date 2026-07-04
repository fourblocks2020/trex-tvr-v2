"""Strategy daemon — rebalancing decision cycle + reconnect + startup (reusable engine).

BAT-001 changes (TVR rebalancing expansion):
  - run_decision_cycle(): inactive + holding slots evaluated; BuySignal + SellSignal dispatched.
  - slots request: strategy="tvr_v1" filter added (build_slots_request extension).
  - strategy_params from slot row: missing → Diagnostic(missing_strategy_params), skip.
  - entry_capital removed: TVR uses target_weight from strategy_params, not fixed KRW.
  - [exit] config is now optional: TVR buy intents have no stop_loss/take_profit.
  - sell reentry guard: sell is re-evaluated every cycle (buy _admitted_slots guard intact).
  - already_active: diagnostics recorded, next cycle re-convergence (no retry, no exception).
  - StatusSnapshot (REQ-004): written atomically after each cycle and on transport error.

The decision cycle uses a 2-phase fan-out:
  Phase 1 (fan-out)   : ThreadPoolExecutor workers fetch current price + evaluate strategy
                        per eligible slot row. Transport is never touched in workers
                        (pika BlockingConnection thread-safety constraint).
  Phase 2 (serial)    : BuySignal / SellSignal results are submitted as intents on the main
                        thread in slots[] response order (deterministic).

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
from trex_tvr.runtime.intent_builder import build_buy_intent, build_sell_intent
from trex_tvr.runtime.status import DepsStatus, StatusSnapshot, write_snapshot
from trex_tvr.strategies import registry
from trex_tvr.strategies.base import BuySignal, Diagnostic, SellSignal
from trex_tvr.transport.base import (
    EventsPort,
    IntentPort,
    RpcTimeoutError,
    SlotPort,
    TransportUnavailableError,
)

_KST = timezone(timedelta(hours=9))

logger = logging.getLogger("trex_tvr")

_ELIGIBLE_STATUSES = {"inactive", "holding"}


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
    """Fan-out worker: fetch current price + evaluate strategy for one slot (Phase 1).

    Returns (eval_asset, decision) where decision is one of:
      BuySignal | SellSignal | HoldDecision | Diagnostic | KisDataHubClientError

    Transport objects are never accessed here — pika BlockingConnection is
    thread-unsafe (architectural invariant).
    """
    code = eval_asset["symbol"]
    try:
        # TVR uses current_price only (candle_count=0); candles are unused.
        market_data = market_data_client.fetch_market_data(
            code,
            now_kst=now,
            candle_count=getattr(strategy, "candle_count", 0),
        )
    except KisDataHubClientError as exc:
        return eval_asset, exc
    # Inject current_price into eval_asset so strategy.evaluate() can read it
    # directly from asset (no separate market_data lookup in pure function).
    eval_asset = {**eval_asset, "current_price": market_data.get("current_price")}
    return eval_asset, strategy.evaluate(eval_asset, slots_resp, market_data)


def _ping_redis(redis_url: str) -> None:
    """Startup gate: verify Redis connectivity (fail-closed)."""
    try:
        import redis as _redis
        _redis.Redis.from_url(redis_url).ping()
    except Exception as exc:
        raise StartupError(f"Redis ping failed: {exc}") from exc


class Daemon:
    """Orchestrates the rebalancing decision cycle over injected ports (testable)."""

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
        status_path: Optional[str] = None,
    ) -> None:
        self.config = config
        self._slot = slot_port
        self._intent = intent_port
        self._events = events_port
        self._market_data = market_data_client
        self._clock = clock
        self._reconnect_fn = reconnect_fn
        self._holiday_cache = holiday_cache
        self._status_path = status_path
        # Same-day reentry guard for BUY only: slot_ids admitted today; reset on new trading day.
        # Sell is re-evaluated every cycle (intent_id dedup at trex-core handles same-day dedup).
        self._admitted_slots: set[str] = set()
        self._tracker_date: Optional[str] = None
        # REQ-004: initialize status snapshot
        self._snapshot = StatusSnapshot()
        registry.ensure_default_strategies_loaded()

    @staticmethod
    def _eligible_for_eval(row: dict) -> Optional[str]:
        """Return a diagnostic reason if a slot row is NOT an eval candidate, else None.

        TVR evaluates both inactive (initial buy) and holding (rebalancing buy/sell) slots.
        """
        if row.get("status") not in _ELIGIBLE_STATUSES:
            return "not_eligible_status"
        if row.get("paused") is True:
            return "paused"
        if row.get("universe_detached") is True:
            return "universe_detached"
        if not row.get("symbol"):
            return "no_symbol"
        return None

    def _slot_for_eval(self, row: dict) -> dict:
        """Map a slot row into the strategy eval asset.

        Injects qty, cash_balance, strategy_params from the slot row.
        current_price is injected later in Phase 1 fan-out worker.
        """
        return {
            "symbol": row.get("symbol"),
            "slot_id": row.get("slot_id"),
            "qty": int(row.get("qty") or row.get("asset_quantity") or 0),
            "cash_balance": int(row.get("cash_balance") or 0),
            "strategy_params": row.get("strategy_params"),
        }

    def _update_snapshot(
        self,
        *,
        cycle_status: str,
        error: Optional[str] = None,
        idle_reason: Optional[str] = None,
        deps: Optional[DepsStatus] = None,
    ) -> None:
        """Update the in-memory snapshot and write it atomically to file (best-effort)."""
        self._snapshot.last_cycle_ts = _now_kst().isoformat()
        self._snapshot.cycle_count += 1
        self._snapshot.last_status = cycle_status
        self._snapshot.last_error = error  # None on normal cycle
        self._snapshot.idle_reason = idle_reason
        if deps is not None:
            self._snapshot.deps = deps
        write_snapshot(self._snapshot, self._status_path)

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
            self._update_snapshot(cycle_status="skipped", idle_reason="out_of_schedule")
            return {"status": "skipped", "reason": "out_of_schedule", "intents": [], "cycle_elapsed_ms": 0}

        # Holiday gate
        if self._holiday_cache is not None and not self._holiday_cache.is_trading_day(
            now.strftime("%Y%m%d")
        ):
            self._update_snapshot(cycle_status="skipped", idle_reason="holiday")
            return {"status": "skipped", "reason": "holiday", "intents": [], "cycle_elapsed_ms": 0}

        trading_date = now.date().isoformat()
        # Reset the buy reentry tracker at a new trading day.
        if self._tracker_date != trading_date:
            self._admitted_slots.clear()
            self._tracker_date = trading_date

        # slots request: include strategy filter (additive — older trex-core ignores it)
        slots_resp = self._slot.request_slots(
            filter="inactive,holding",
            strategy=self.config.strategy_id,
        )
        if slots_resp.get("degraded"):
            self._update_snapshot(cycle_status="degraded_hold")
            return {"status": "degraded_hold", "reason": slots_resp.get("reason"), "intents": [], "cycle_elapsed_ms": 0}

        # Capacity hint gate (available_slots == 0 means all slots occupied for buy)
        if slots_resp.get("available_slots") == 0:
            self._update_snapshot(cycle_status="capacity_full_hint_skip", idle_reason="capacity_full_hint_skip")
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
            self._update_snapshot(cycle_status="no_op")
            return {"status": "no_op", "slots": 0, "intents": [], "cycle_elapsed_ms": 0}

        # Eligibility filter
        diagnostics: list[dict] = []
        eligible: list[tuple[dict, dict]] = []
        for row in slots:
            excluded = self._eligible_for_eval(row)
            if excluded is not None:
                diagnostics.append(
                    {"slot_id": row.get("slot_id"), "reason": excluded, "kind": "excluded"}
                )
                continue
            # strategy_params missing fallback (REQ-003): skip without buy/sell
            if not row.get("strategy_params"):
                diagnostics.append(
                    {"slot_id": row.get("slot_id"), "reason": "missing_strategy_params", "kind": "diagnostic"}
                )
                continue
            eval_asset = self._slot_for_eval(row)
            eligible.append((row, eval_asset))

        if not eligible:
            self._update_snapshot(cycle_status="no_op")
            return {"status": "no_op", "slots": len(slots), "intents": [], "diagnostics": diagnostics, "cycle_elapsed_ms": 0}

        # Cycle timer starts after eligibility filter.
        cycle_start = time.monotonic()
        strategy = strategies[0]

        # Phase 1: fan-out — current price fetch + strategy evaluate in parallel workers.
        raw_results: list[tuple[dict, Any]] = []
        with ThreadPoolExecutor(max_workers=self.config.worker_count) as executor:
            futures_map = [
                (eval_asset, executor.submit(_eval_slot_work, eval_asset, slots_resp, strategy, self._market_data, now))
                for _, eval_asset in eligible
            ]
            for eval_asset, fut in futures_map:
                try:
                    updated_asset, decision = fut.result()
                    raw_results.append((updated_asset, decision))
                except Exception as exc:
                    raw_results.append(
                        (eval_asset, KisDataHubClientError(str(exc), error_class="other"))
                    )

        # Check for systemic auth failure before Phase 2.
        for eval_asset, decision in raw_results:
            if isinstance(decision, KisDataHubClientError) and decision.error_class == "auth":
                diagnostics.append({"symbol": eval_asset["symbol"], "error_class": "auth"})
                cycle_elapsed_ms = int((time.monotonic() - cycle_start) * 1000)
                self._update_snapshot(cycle_status="aborted", error="kis_auth_failure")
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
                # Buy reentry guard: slot already admitted today -> skip
                if slot_id in self._admitted_slots:
                    diagnostics.append(
                        {"slot_id": slot_id, "reason": "reentry_skip", "kind": "skipped"}
                    )
                    continue
                # Build buy payload — no stop_loss/take_profit (TVR pure rebalancing, [exit] optional)
                buy_kwargs: dict = dict(
                    trading_date=trading_date,
                    slot_id=slot_id,
                    strategy_id=strategy.strategy_id,
                    timeout_sec=self.config.execution.timeout_sec,
                    slippage_pct=self.config.execution.slippage_pct,
                )
                if self.config.exit is not None:
                    buy_kwargs["stop_loss_pct"] = self.config.exit.stop_loss_pct
                    buy_kwargs["take_profit_pct"] = self.config.exit.take_profit_pct
                else:
                    # TVR: no exit thresholds on buy intent
                    buy_kwargs["stop_loss_pct"] = 0.0
                    buy_kwargs["take_profit_pct"] = 0.0
                payload = build_buy_intent(decision, **buy_kwargs)
                # Remove stop_loss_price / take_profit_price when exit is None (TVR)
                if self.config.exit is None:
                    payload.pop("stop_loss_price", None)
                    payload.pop("take_profit_price", None)
                response = self._intent.submit_buy(payload)
                intents.append({"payload": payload, "response": response})
                if response.get("status") == "admitted":
                    self._admitted_slots.add(slot_id)
                elif response.get("status") == "rejected":
                    reason = response.get("reason")
                    diagnostics.append(
                        {"slot_id": slot_id, "reason": reason, "kind": "rejected"}
                    )
                    if reason == "already_active":
                        # REQ-003 AC-5: log and wait for next cycle re-convergence
                        logger.debug("buy already_active slot_id=%s, will re-converge next cycle", slot_id)
            elif isinstance(decision, SellSignal):
                if not slot_id:
                    diagnostics.append(
                        {"symbol": eval_asset["symbol"], "reason": "missing_slot_id", "kind": "diagnostic"}
                    )
                    continue
                # Sell: no reentry guard — re-evaluated every cycle (trex-core dedup via intent_id)
                sell_payload = build_sell_intent(
                    decision,
                    trading_date=trading_date,
                    slot_id=slot_id,
                    strategy_id=strategy.strategy_id,
                )
                sell_response = self._intent.submit_sell(sell_payload)
                intents.append({"payload": sell_payload, "response": sell_response})
                if sell_response.get("status") == "rejected":
                    reason = sell_response.get("reason")
                    diagnostics.append(
                        {"slot_id": slot_id, "reason": reason, "kind": "rejected"}
                    )
                    if reason == "already_active":
                        logger.debug("sell already_active slot_id=%s, will re-converge next cycle", slot_id)
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
        self._update_snapshot(cycle_status="ok")
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
                    # Update snapshot with transport error (REQ-004 AC-4)
                    self._snapshot.last_error = str(exc)
                    self._snapshot.deps.rabbitmq = "error"
                    write_snapshot(self._snapshot, self._status_path)
                    time.sleep(delay)
                    backoff_attempt += 1
                    if self._reconnect_fn is not None:
                        try:
                            self._reconnect_fn()
                            logger.info("reconnected after %d attempt(s)", backoff_attempt)
                            self._snapshot.deps.rabbitmq = "ok"
                            write_snapshot(self._snapshot, self._status_path)
                            backoff_attempt = 0
                        except Exception as reconnect_exc:
                            logger.error("reconnect failed: %s", reconnect_exc)
        except KeyboardInterrupt:
            logger.info("shutdown: KeyboardInterrupt")
            return


def build_daemon(config: Config, status_path: Optional[str] = None) -> tuple["Daemon", Callable[[], None]]:
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
    # Initial deps status for snapshot
    deps = DepsStatus()
    try:
        logger.info("startup: connecting to RabbitMQ ...")
        conn.connect()
        logger.info("startup: RabbitMQ connected")
        deps.rabbitmq = "ok"
        logger.info("startup: probing kis-data-hub /health (%s) ...", config.kis_data_hub.base_url)
        kis.check_health()
        logger.info("startup: kis-data-hub healthy")
        deps.kis_data_hub = "ok"
        logger.info("startup: pinging Redis (%s) ...", config.redis_url)
        _ping_redis(config.redis_url)
        logger.info("startup: Redis ping OK")
        deps.redis = "ok"
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
        market_data_client=kis,
        reconnect_fn=conn.reconnect,
        holiday_cache=HolidayCache(config.redis_url, kis),
        status_path=status_path,
    )
    # Write initial snapshot after successful startup
    daemon._snapshot.deps = deps
    daemon._snapshot.last_status = "started"
    write_snapshot(daemon._snapshot, status_path)

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
