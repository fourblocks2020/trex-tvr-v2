"""Pure wire framing + Port protocols + error contract for trex-core transport.

No pika / network here — these helpers are deterministic and unit-testable, and
serve as the snapshot/intent/event schema guards. The `Pika*`
clients build on them.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

_INTENT_STATUSES = ("admitted", "rejected")


# --- Error contract (interface-contract.md) -------------------------------------


class TransportError(Exception):
    """Base class for transport-layer failures."""


class TransportUnavailableError(TransportError):
    """Publish/RPC attempted while not connected. No local buffering — fail fast."""


class RpcTimeoutError(TransportError):
    """No RPC reply within the configured timeout."""


class WireSchemaError(TransportError):
    """A received payload did not match the expected wire contract (drift guard)."""


# --- Port protocols (daemon depends on these; tests inject stubs) ---------------


@runtime_checkable
class SlotPort(Protocol):
    def request_slots(self, *, filter: str) -> dict: ...


@runtime_checkable
class IntentPort(Protocol):
    def submit_buy(self, payload: dict) -> dict: ...


@runtime_checkable
class EventsPort(Protocol):
    def poll_events(self, max_messages: int = 100) -> list[dict]: ...


# --- Queue naming (interface-contract.md: {base}.{instance_key}) -----------------


def queue_name(base: str, instance_key: str) -> str:
    """`{base}.{instance_key}`; `instance_key="default"` keeps the literal base name."""
    if not instance_key or instance_key == "default":
        return base
    return f"{base}.{instance_key}"


# --- Request builders -----------------------------------------------------------


def build_slots_request(filter: str) -> dict:
    """Build a `trex.slots.req` body (REL-006).

    Carries only `{filter}` — `spy` is intentionally NOT sent so the server default
    (`spy=false`) applies. A buy daemon only ever asks for buyable
    (`status=inactive`) slots, so holding-slot watchdog control (spy) is irrelevant.
    """
    if not isinstance(filter, str) or not filter:
        raise WireSchemaError(f"invalid slots filter {filter!r}; expected a non-empty string")
    return {"filter": filter}


# --- Response / event parsers (schema guards) -----------------------------------


def _require_dict(body: Any, what: str) -> dict:
    if not isinstance(body, dict):
        raise WireSchemaError(f"{what} is not a JSON object")
    return body


def parse_slots_response(body: Any) -> dict:
    """Validate + normalize a REL-006 slots envelope.

    Envelope: `{slots[], available_slots: int|null, degraded, reason, message}`.
    Each slot row must carry at least `slot_id` + `status` (drift guard — keyed on
    the `slots[]` list being absent/non-list, NOT on any single field). New additive
    fields (`cash_balance`, `asset_quantity`, `warned`, `cash_estimated`, `watchdog`,
    `paused`, `universe_detached`, ...) and unknown fields pass through untouched
    (additive backward-compat).

    `available_slots` (trex-core BAT-037) is a server-computed capacity
    hint: `max(0, M − active)` when `max_active_slots` is set, else `null`. It is
    normalized to `int | None` — a missing field (older core) or `null` both become
    `None`. The old snapshot envelope (`assets[]` without
    `slots[]`) still raises WireSchemaError.
    """
    data = _require_dict(body, "slots response")
    slots = data.get("slots")
    if not isinstance(slots, list):
        raise WireSchemaError("slots response missing 'slots' list")
    for i, row in enumerate(slots):
        if not isinstance(row, dict):
            raise WireSchemaError(f"slots[{i}] is not a JSON object")
        for required in ("slot_id", "status"):
            if required not in row:
                raise WireSchemaError(f"slots[{i}] missing {required!r}")
    raw_avail = data.get("available_slots")
    # int|None only; bool is excluded (JSON true/false is not a slot count).
    available_slots = raw_avail if isinstance(raw_avail, int) and not isinstance(raw_avail, bool) else None
    return {
        "slots": slots,
        "available_slots": available_slots,
        "degraded": bool(data.get("degraded", False)),
        "reason": data.get("reason"),
        "message": data.get("message"),
    }


def parse_intent_response(body: Any) -> dict:
    """Validate + normalize an intent response (`admitted` | `rejected`)."""
    data = _require_dict(body, "intent response")
    if "intent_id" not in data:
        raise WireSchemaError("intent response missing 'intent_id'")
    status = data.get("status")
    if status not in _INTENT_STATUSES:
        raise WireSchemaError(f"intent response status {status!r} not in {_INTENT_STATUSES}")
    return {
        "intent_id": data["intent_id"],
        "status": status,
        "slot_id": data.get("slot_id"),
        "reason": data.get("reason"),
    }


def parse_event(body: Any) -> dict:
    """Validate + normalize a `trex.events` message."""
    data = _require_dict(body, "event")
    for required in ("event_type", "slot_id", "sequence_at"):
        if required not in data:
            raise WireSchemaError(f"event missing {required!r}")
    return {
        "event_type": data["event_type"],
        "slot_id": data["slot_id"],
        "strategy_id": data.get("strategy_id"),
        "symbol": data.get("symbol"),
        "status": data.get("status"),
        "qty": data.get("qty"),
        "avg_price": data.get("avg_price"),
        "portfolio_id": data.get("portfolio_id"),
        "trading_date": data.get("trading_date"),
        "sequence_at": data["sequence_at"],
    }
