"""Legacy compatibility event subscriber; not wired into the TVR runtime.

Issue #5 makes decision execution snapshot-only: ``build_daemon`` does not construct
this consumer and ``run_decision_cycle`` does not poll it. The module remains only to
avoid breaking external imports while that legacy surface is retired separately.
"""
from __future__ import annotations

import json

from trex_tvr.transport.base import parse_event
from trex_tvr.transport.connection import PikaConnection

_EVENTS_EXCHANGE = "trex.events"


class PikaEventsConsumer:
    """Implements EventsPort by draining the per-strategy events queue (basic_get)."""

    def __init__(self, connection: PikaConnection, strategy_id: str) -> None:
        self._conn = connection
        self._queue = f"trex.events.strategy.{strategy_id}"
        binding_key = f"*.{strategy_id}.#"
        channel = connection.channel
        channel.queue_declare(queue=self._queue, durable=True)
        channel.queue_bind(queue=self._queue, exchange=_EVENTS_EXCHANGE, routing_key=binding_key)

    def poll_events(self, max_messages: int = 100) -> list[dict]:
        channel = self._conn.channel  # raises TransportUnavailableError if not connected
        events: list[dict] = []
        for _ in range(max_messages):
            method, _props, body = channel.basic_get(queue=self._queue, auto_ack=True)
            if method is None:
                break
            events.append(parse_event(json.loads(body)))
        return events
