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

BAT-002 changes (session calendar + cadence + fan-in):
  - REQ-005: session gate (session_calendar.get_session_info) before each cycle.
      is_tradeable=False → session_hold. is_nxt_only=True → markets gate per slot.
      NXT gate: row.markets has no "NXT" → intent suppressed, Diagnostic(market_not_eligible).
      kdh quote failure in NXT session → Diagnostic(quote_unavailable).
  - REQ-006: poll_interval (int, seconds) replaces poll_interval_seconds.
      drain/floor loop: elapsed >= poll_interval → no sleep. elapsed < poll_interval → sleep remainder.
  - REQ-007: config.instances list → per-instance SlotPort/IntentPort map (fan-in).
      Slots collected from each instance, tagged with _source_instance.
      Intents routed to source instance's IntentPort.
      intent_id includes instance_key: {strategy}:{instance}:{date}:{slot}.
      Partial failure: partial_degraded + continue. Total failure: degraded_hold.

Issue #5 snapshot-only boundary:
  - Broker execution events are not consumed by the decision cycle.
  - A sell decision id is scoped to the current snapshot cycle, so a later holding
    snapshot can create an independent intent without broker retry metadata.

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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from trex_tvr.market_data.kis_data_hub_client import KisDataHubClient, KisDataHubClientError
from trex_tvr.runtime.config import Config, load_config
from trex_tvr.runtime.intent_builder import build_buy_intent, build_sell_intent
from trex_tvr.runtime.session_calendar import get_session_info
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


def _has_nxt_market(row: dict) -> bool:
    """Return True if row.markets contains 'NXT'.

    REQ-005 fallback contract: markets absent or [] → conservative ["KRX"] → NXT not present.
    """
    markets = row.get("markets")
    if not markets:
        # absent or empty → conservative: treat as ["KRX"]
        return False
    return "NXT" in markets


def _resolve_intent_venue(*, nxt_only: bool) -> str:
    return "NXT" if nxt_only else "KRX"


class Daemon:
    """Orchestrates the rebalancing decision cycle over injected ports (testable).

    BAT-002: slot_ports / intent_ports are per-instance maps (fan-in).
    Legacy single-port (slot_port / intent_port) arguments are accepted for
    backward compatibility and auto-wrapped into single-entry maps.
    """

    def __init__(
        self,
        config: Config,
        *,
        # BAT-002 REQ-007: per-instance maps (primary).
        slot_ports: dict[str, SlotPort] | None = None,
        intent_ports: dict[str, IntentPort] | None = None,
        # Legacy single-port (backward compat — tests, build_daemon pre-fanin).
        slot_port: SlotPort | None = None,
        intent_port: IntentPort | None = None,
        events_port: EventsPort | None = None,
        market_data_client: Any,
        clock: Callable[[], datetime] = _now_kst,
        reconnect_fn: Callable[[], None] | None = None,
        idle_wait_fn: Callable[[float], None] | None = None,
        holiday_cache: Any = None,
        status_path: str | None = None,
    ) -> None:
        self.config = config

        # Resolve per-instance port maps (fan-in, REQ-007).
        # Priority: explicit slot_ports/intent_ports > legacy single slot_port/intent_port.
        if slot_ports is not None:
            self._slot_ports: dict[str, SlotPort] = slot_ports
        elif slot_port is not None:
            # Legacy: wrap single port under the config's primary instance_key or "default".
            _key = config.instances[0] if config.instances else config.instance_key
            self._slot_ports = {_key: slot_port}
        else:
            raise ValueError("Either slot_ports or slot_port must be provided")

        if intent_ports is not None:
            self._intent_ports: dict[str, IntentPort] = intent_ports
        elif intent_port is not None:
            _key = config.instances[0] if config.instances else config.instance_key
            self._intent_ports = {_key: intent_port}
        else:
            raise ValueError("Either intent_ports or intent_port must be provided")

        # Keep legacy single-port references for backward compat (tests that use .slot_port).
        self._slot = next(iter(self._slot_ports.values()))
        self._intent = next(iter(self._intent_ports.values()))

        # Compatibility injection seam only. Decisions consume slot snapshots;
        # broker/fill events are not polled or used as retry input.
        self._events = events_port
        self._market_data = market_data_client
        self._clock = clock
        self._reconnect_fn = reconnect_fn
        self._idle_wait_fn = idle_wait_fn
        self._holiday_cache = holiday_cache
        self._status_path = status_path
        # Same-day reentry guard for BUY only: slot_ids admitted today; reset on new trading day.
        # Sell is re-evaluated every cycle (intent_id dedup at trex-core handles same-day dedup).
        self._admitted_slots: set[str] = set()
        self._tracker_date: str | None = None
        # REQ-004: initialize status snapshot
        self._snapshot = StatusSnapshot()
        registry.ensure_default_strategies_loaded()

    @staticmethod
    def _eligible_for_eval(row: dict) -> str | None:
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
        error: str | None = None,
        idle_reason: str | None = None,
        deps: DepsStatus | None = None,
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

    def _collect_slots_fanin(
        self, strategy_id: str
    ) -> tuple[list[dict], list[str]]:
        """Fan-in: collect slots from all configured instances.

        Returns:
          (merged_rows, partial_degraded_keys)

        Each row is tagged with `_source_instance`. If ALL instances fail → raises
        RuntimeError('all_degraded') — caller handles as degraded_hold.
        """
        merged: list[dict] = []
        partial_degraded: list[str] = []

        for instance_key, slot_port in self._slot_ports.items():
            try:
                resp = slot_port.request_slots(
                    filter="inactive,holding",
                    strategy=strategy_id,
                )
                if resp.get("degraded"):
                    logger.warning(
                        "fanin: instance=%s slots.req degraded reason=%s",
                        instance_key, resp.get("reason"),
                    )
                    partial_degraded.append(instance_key)
                    continue
                rows = resp.get("slots") or []
                for row in rows:
                    merged.append({**row, "_source_instance": instance_key})
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "fanin: instance=%s slots.req failed: %s; partial_degraded",
                    instance_key, exc,
                )
                partial_degraded.append(instance_key)

        return merged, partial_degraded

    def run_decision_cycle(self, now_kst: datetime | None = None) -> dict:
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

        # REQ-005: session gate (BAT-002).
        window, tradeable, nxt_only = get_session_info(now, self._holiday_cache)
        if not tradeable:
            self._update_snapshot(cycle_status="session_hold", idle_reason="session_hold")
            logger.info("session_hold: window=%s", window.name)
            return {
                "status": "session_hold",
                "reason": window.name,
                "window": window.name,
                "intents": [],
                "cycle_elapsed_ms": 0,
            }

        # Legacy holiday gate (holiday_cache used by session_calendar now; keep for safety).
        # Note: session_calendar already checks holiday via get_session_info above.
        # This block is a belt-and-suspenders fallback only if holiday_cache is wired separately.
        intent_venue = _resolve_intent_venue(nxt_only=nxt_only)
        trading_date = now.date().isoformat()
        # Reset the buy reentry tracker at a new trading day.
        if self._tracker_date != trading_date:
            self._admitted_slots.clear()
            self._tracker_date = trading_date

        # REQ-007: fan-in slot collection from all configured instances.
        if len(self._slot_ports) > 1:
            # Multi-instance fan-in path.
            merged_rows, partial_degraded_keys = self._collect_slots_fanin(self.config.strategy_id)
            all_instances = set(self._slot_ports.keys())
            failed_instances = set(partial_degraded_keys)
            if failed_instances == all_instances:
                # Total failure: degraded_hold.
                logger.warning("fanin: all instances degraded → degraded_hold")
                self._update_snapshot(cycle_status="degraded_hold")
                return {
                    "status": "degraded_hold",
                    "reason": "all_instances_degraded",
                    "intents": [],
                    "cycle_elapsed_ms": 0,
                }
            if partial_degraded_keys:
                logger.warning("fanin: partial_degraded instances=%s", partial_degraded_keys)
            slots = merged_rows
            # Build a synthetic slots_resp for strategy evaluate (uses merged rows).
            slots_resp = {"slots": slots, "degraded": bool(partial_degraded_keys), "reason": None}
        else:
            # Single-instance path (legacy / default).
            instance_key = next(iter(self._slot_ports))
            slot_port = self._slot_ports[instance_key]
            slots_resp = slot_port.request_slots(
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

            raw_slots = slots_resp.get("slots") or []
            # Tag rows with source instance for uniform routing.
            slots = [{**row, "_source_instance": instance_key} for row in raw_slots]

        if not slots:
            self._update_snapshot(cycle_status="no_op")
            return {"status": "no_op", "slots": 0, "intents": [], "cycle_elapsed_ms": 0}

        # Eligibility filter + REQ-005 NXT markets gate.
        diagnostics: list[dict] = []
        eligible: list[tuple[dict, dict]] = []
        for row in slots:
            excluded = self._eligible_for_eval(row)
            if excluded is not None:
                diagnostics.append(
                    {"slot_id": row.get("slot_id"), "reason": excluded, "kind": "excluded"}
                )
                continue
            # REQ-005: NXT markets gate — if NXT-only session and slot has no NXT market.
            if nxt_only and not _has_nxt_market(row):
                diagnostics.append({
                    "slot_id": row.get("slot_id"),
                    "reason": "market_not_eligible",
                    "session": window.name,
                    "kind": "diagnostic",
                })
                continue
            # strategy_params missing fallback (REQ-003): skip without buy/sell
            if not row.get("strategy_params"):
                diagnostics.append(
                    {"slot_id": row.get("slot_id"), "reason": "missing_strategy_params", "kind": "diagnostic"}
                )
                continue
            eval_asset = self._slot_for_eval(row)
            # Preserve source instance for Phase 2 routing.
            eval_asset["_source_instance"] = row.get("_source_instance", self.config.instance_key)
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
                except Exception as exc:  # noqa: BLE001
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
            source_instance = eval_asset.get("_source_instance", self.config.instance_key)

            if isinstance(decision, KisDataHubClientError):
                # REQ-005: NXT session quote failure → quote_unavailable diagnostic.
                if nxt_only:
                    diagnostics.append({
                        "slot_id": slot_id,
                        "symbol": eval_asset["symbol"],
                        "reason": "quote_unavailable",
                        "session": window.name,
                        "kind": "diagnostic",
                    })
                else:
                    diagnostics.append({"symbol": eval_asset["symbol"], "error_class": decision.error_class})
                continue
            if isinstance(decision, BuySignal):
                if not slot_id:
                    diagnostics.append(
                        {"symbol": eval_asset["symbol"], "reason": "missing_slot_id", "kind": "diagnostic"}
                    )
                    continue
                # Buy reentry guard: slot already admitted today -> skip
                # Use (instance, slot_id) as the reentry key (REQ-007).
                reentry_key = f"{source_instance}:{slot_id}"
                if reentry_key in self._admitted_slots:
                    diagnostics.append(
                        {"slot_id": slot_id, "reason": "reentry_skip", "kind": "skipped"}
                    )
                    continue
                # Build buy payload — no stop_loss/take_profit (TVR pure rebalancing, [exit] optional)
                buy_kwargs: dict = {
                    "trading_date": trading_date,
                    "slot_id": slot_id,
                    "strategy_id": strategy.strategy_id,
                    "venue": intent_venue,
                    "instance_key": source_instance,
                    "timeout_sec": self.config.execution.timeout_sec,
                    "slippage_pct": self.config.execution.slippage_pct,
                }
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
                # REQ-007: route to source instance's intent port.
                intent_port = self._intent_ports.get(source_instance, self._intent)
                response = intent_port.submit_buy(payload)
                intents.append({"payload": payload, "response": response})
                if response.get("status") == "admitted":
                    self._admitted_slots.add(reentry_key)
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
                    venue=intent_venue,
                    instance_key=source_instance,
                    decision_at=now.isoformat(),
                )
                # REQ-007: route to source instance's intent port.
                intent_port = self._intent_ports.get(source_instance, self._intent)
                sell_response = intent_port.submit_sell(sell_payload)
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
        budget_ms = int(self.config.poll_interval * 1000)
        if cycle_elapsed_ms > budget_ms:
            logger.warning(
                "cycle budget exceeded: elapsed=%dms budget=%dms slots=%d",
                cycle_elapsed_ms, budget_ms, len(eligible),
            )

        result = {
            "status": "ok",
            "slots": len(slots),
            "intents": intents,
            "diagnostics": diagnostics,
            "cycle_elapsed_ms": cycle_elapsed_ms,
        }
        self._update_snapshot(cycle_status="ok")
        if logger.isEnabledFor(logging.INFO):
            logger.info(
                "cycle ok slots=%d eligible=%d intents=%d diagnostics=%d elapsed=%dms",
                len(slots), len(eligible), len(intents), len(diagnostics), cycle_elapsed_ms,
            )
        return result

    def run_forever(self, _max_cycles: int | None = None) -> None:
        logger.info(
            "started: instance=%s strategy=%s poll=%ss instances=%s",
            self.config.instance_key,
            self.config.strategy_id,
            self.config.poll_interval,
            self.config.instances,
        )
        backoff_attempt = 0
        cycles_run = 0
        try:
            while _max_cycles is None or cycles_run < _max_cycles:
                # REQ-006: monotonic drain/floor loop.
                t_start = time.monotonic()
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
                    # REQ-006 drain/floor: sleep only the remaining time.
                    elapsed = time.monotonic() - t_start
                    remaining = self.config.poll_interval - elapsed
                    if remaining > 0:
                        if self._idle_wait_fn is not None:
                            # BlockingConnection has no background I/O thread. Pumping
                            # events while idle keeps negotiated heartbeats alive and
                            # turns a reset into a typed transport failure.
                            self._idle_wait_fn(remaining)
                        else:
                            time.sleep(remaining)
                    # else: no sleep — drain immediately into next cycle.
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
                        except Exception as reconnect_exc:  # noqa: BLE001
                            logger.error("reconnect failed: %s", reconnect_exc)
        except KeyboardInterrupt:
            logger.info("shutdown: KeyboardInterrupt")
            return


def build_daemon(config: Config, status_path: str | None = None) -> tuple[Daemon, Callable[[], None]]:
    """Wire the real pika + kis-data-hub + Redis dependencies. Fail-closed startup.

    BAT-002 REQ-007: creates per-instance SlotPort/IntentPort maps from config.instances.
    """
    from trex_tvr.transport.connection import PikaConnection
    from trex_tvr.transport.intent_client import PikaIntentClient
    from trex_tvr.transport.slot_client import PikaSlotClient

    registry.ensure_default_strategies_loaded()
    failures = registry.precheck_instantiate_all()
    if failures:
        raise RuntimeError(f"strategy registry precheck failed: {failures}")

    conn = PikaConnection(
        config.rabbitmq_url,
        heartbeat_seconds=config.rabbitmq_connection.heartbeat_seconds,
        blocked_connection_timeout_seconds=(
            config.rabbitmq_connection.blocked_connection_timeout_seconds
        ),
        socket_timeout_seconds=config.rabbitmq_connection.socket_timeout_seconds,
    )
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

    # REQ-007: build per-instance port maps.
    slot_ports: dict[str, SlotPort] = {}
    intent_ports: dict[str, IntentPort] = {}
    for inst_key in config.instances:
        slot_ports[inst_key] = PikaSlotClient(conn, inst_key, config.rpc_timeout_seconds)
        intent_ports[inst_key] = PikaIntentClient(conn, inst_key, config.rpc_timeout_seconds)

    daemon = Daemon(
        config,
        slot_ports=slot_ports,
        intent_ports=intent_ports,
        market_data_client=kis,
        reconnect_fn=conn.reconnect,
        idle_wait_fn=conn.wait_with_heartbeats,
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
    config_path: str, env_file: str | None = None, verbose: bool = False
) -> int:  # pragma: no cover - integration entry.
    configure_logging(verbose)
    config = load_config(config_path, env_file=env_file)
    daemon, shutdown = build_daemon(config)
    try:
        daemon.run_forever()
    finally:
        shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin wrapper.
    from trex_tvr.runtime.cli import main as cli_main

    return cli_main(argv)
