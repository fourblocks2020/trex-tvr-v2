"""trex-core RabbitMQ wire client (pure AMQP).

This package implements the trex-core wire contract (slots RPC, intent RPC,
events subscription) over pika. It MUST NOT import `trex_core` — coupling is the
JSON wire payload only (enforced by tests/test_import_sweep.py).

Pure wire framing (request builders, response/event parsers, errors, Port
protocols) lives in `base.py` so it can be unit-tested without a broker. The
`Pika*` clients add the I/O.
"""

from trex_tvr.transport.base import (
    EventsPort,
    IntentPort,
    RpcTimeoutError,
    SlotPort,
    TransportError,
    TransportUnavailableError,
    WireSchemaError,
    build_slots_request,
    parse_event,
    parse_intent_response,
    parse_slots_response,
    queue_name,
)

__all__ = [
    "EventsPort",
    "IntentPort",
    "SlotPort",
    "TransportError",
    "TransportUnavailableError",
    "RpcTimeoutError",
    "WireSchemaError",
    "build_slots_request",
    "parse_event",
    "parse_intent_response",
    "parse_slots_response",
    "queue_name",
]
