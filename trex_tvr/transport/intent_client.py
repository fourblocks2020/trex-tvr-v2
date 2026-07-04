"""intent RPC client (`trex.intent.req` buy admission).

The buy intent payload is built by the runtime intent builder. This
client publishes it with `correlation_id = payload["intent_id"]` as the idempotency
key: retrying the same cycle reuses the same intent_id, so trex-core's
intent_log dedups duplicate admissions. trex-core owns the slot transition.
"""
from __future__ import annotations

import json

from trex_tvr.transport.base import (
    WireSchemaError,
    parse_intent_response,
    parse_sell_intent_response,
    queue_name,
)
from trex_tvr.transport.connection import PikaConnection

_INTENT_BASE = "trex.intent.req"
_DELIVERY_MODE_PERSISTENT = 2


class PikaIntentClient:
    """Implements IntentPort over pika direct reply-to RPC."""

    def __init__(
        self, connection: PikaConnection, instance_key: str = "default", rpc_timeout: float = 5.0
    ) -> None:
        self._conn = connection
        self._routing_key = queue_name(_INTENT_BASE, instance_key)
        self._rpc_timeout = rpc_timeout

    def submit_buy(self, payload: dict) -> dict:
        intent_id = payload.get("intent_id")
        if not intent_id:
            raise WireSchemaError("buy intent payload missing 'intent_id' (idempotency key)")
        raw = self._conn.rpc_call(
            self._routing_key,
            json.dumps(payload).encode("utf-8"),
            correlation_id=str(intent_id),
            timeout=self._rpc_timeout,
            delivery_mode=_DELIVERY_MODE_PERSISTENT,
        )
        return parse_intent_response(json.loads(raw))

    def submit_sell(self, payload: dict) -> dict:
        """Submit a sell intent RPC to trex-core.

        Uses the same routing key as buy (`trex.intent.req.<instance_key>`).
        trex-core dispatches buy vs sell by the `intent_type` field in the payload.
        The idempotency key (`intent_id`) is deterministic per (strategy_id, trading_date, slot_id)
        with a `sell:` namespace prefix to avoid collision with buy keys.
        """
        intent_id = payload.get("intent_id")
        if not intent_id:
            raise WireSchemaError("sell intent payload missing 'intent_id' (idempotency key)")
        raw = self._conn.rpc_call(
            self._routing_key,
            json.dumps(payload).encode("utf-8"),
            correlation_id=str(intent_id),
            timeout=self._rpc_timeout,
            delivery_mode=_DELIVERY_MODE_PERSISTENT,
        )
        return parse_sell_intent_response(json.loads(raw))
