"""slots RPC client (`trex.slots.req`).

The slots poll doubles as the holding-slot watchdog liveness signal (server default
`spy=false`). This buy daemon only requests buyable (`status=inactive`) slots — `spy`
is not sent. Slot rows carry no price; market data comes from kis-data-hub.
"""
from __future__ import annotations

import json
import uuid

from trex_tvr.transport.base import build_slots_request, parse_slots_response, queue_name
from trex_tvr.transport.connection import PikaConnection

_SLOTS_BASE = "trex.slots.req"


class PikaSlotClient:
    """Implements SlotPort over pika direct reply-to RPC."""

    def __init__(
        self, connection: PikaConnection, instance_key: str = "default", rpc_timeout: float = 5.0
    ) -> None:
        self._conn = connection
        self._routing_key = queue_name(_SLOTS_BASE, instance_key)
        self._rpc_timeout = rpc_timeout

    def request_slots(self, *, filter: str) -> dict:
        body = build_slots_request(filter)
        raw = self._conn.rpc_call(
            self._routing_key,
            json.dumps(body).encode("utf-8"),
            correlation_id=str(uuid.uuid4()),
            timeout=self._rpc_timeout,
        )
        return parse_slots_response(json.loads(raw))
