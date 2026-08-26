"""Transport wire-framing unit tests.

REQ-003 AC-1 (QC MAJOR fix): verifies that build_slots_request produces a body
containing the strategy field, and that PikaSlotClient passes it through to the
wire call.
"""
from __future__ import annotations

import json

import pytest

from trex_tvr.transport.base import WireSchemaError, build_slots_request

# ---------------------------------------------------------------------------
# build_slots_request
# ---------------------------------------------------------------------------

def test_build_slots_request_includes_strategy():
    """REQ-003 AC-1: strategy key appears in the built body when supplied."""
    body = build_slots_request("inactive,holding", strategy="tvr_v1")
    assert body["filter"] == "inactive,holding"
    assert body["strategy"] == "tvr_v1"


def test_build_slots_request_without_strategy_omits_key():
    """Additive: when strategy is None the key is absent (backward-compat)."""
    body = build_slots_request("inactive,holding")
    assert "strategy" not in body


def test_build_slots_request_empty_filter_raises():
    """build_slots_request rejects an empty filter string."""
    with pytest.raises(WireSchemaError):
        build_slots_request("")


def test_build_slots_request_json_round_trip():
    """Wire body serialises / deserialises cleanly with strategy present."""
    body = build_slots_request("inactive,holding", strategy="tvr_v1")
    recovered = json.loads(json.dumps(body))
    assert recovered["strategy"] == "tvr_v1"
    assert recovered["filter"] == "inactive,holding"


# ---------------------------------------------------------------------------
# PikaSlotClient — strategy forwarded to rpc_call body
# ---------------------------------------------------------------------------

class _StubConnection:
    """Captures the raw bytes passed to rpc_call so we can inspect the wire body."""

    def __init__(self, rpc_response: dict) -> None:
        self._rpc_response = rpc_response
        self.last_body_bytes: bytes | None = None
        self.last_correlation_id: str | None = None

    def rpc_call(
        self,
        routing_key: str,
        body: bytes,
        *,
        correlation_id: str,
        timeout: float,
        delivery_mode: int | None = None,
    ) -> bytes:
        self.last_body_bytes = body
        self.last_correlation_id = correlation_id
        return json.dumps(self._rpc_response).encode("utf-8")


def _make_slots_response(slots=None):
    return {
        "slots": slots or [],
        "available_slots": None,
        "degraded": False,
        "reason": None,
        "message": None,
    }


def test_pika_slot_client_includes_strategy_in_wire_body():
    """REQ-003 AC-1: PikaSlotClient.request_slots(strategy=...) puts strategy in wire JSON."""
    from trex_tvr.transport.slot_client import PikaSlotClient

    conn = _StubConnection(_make_slots_response())
    client = PikaSlotClient(conn, instance_key="default")

    client.request_slots(filter="inactive,holding", strategy="tvr_v1")

    assert conn.last_body_bytes is not None
    wire = json.loads(conn.last_body_bytes)
    assert wire["filter"] == "inactive,holding"
    assert wire["strategy"] == "tvr_v1", (
        f"strategy key missing from wire body: {wire!r}"
    )


def test_pika_slot_client_omits_strategy_when_none():
    """Additive: no strategy key when strategy=None (backward-compat)."""
    from trex_tvr.transport.slot_client import PikaSlotClient

    conn = _StubConnection(_make_slots_response())
    client = PikaSlotClient(conn, instance_key="default")

    client.request_slots(filter="inactive")

    wire = json.loads(conn.last_body_bytes)
    assert "strategy" not in wire


def test_pika_intent_client_buy_preserves_venue():
    from trex_tvr.transport.intent_client import PikaIntentClient

    conn = _StubConnection({"intent_id": "buy-1", "status": "admitted", "slot_id": "slot-1"})
    client = PikaIntentClient(conn, instance_key="default")

    result = client.submit_buy({
        "intent_type": "buy",
        "intent_id": "buy-1",
        "slot_id": "slot-1",
        "qty": 1,
        "entry_price": 1000,
        "strategy_id": "tvr_v1",
        "venue": "KRX",
    })

    wire = json.loads(conn.last_body_bytes)
    assert wire["venue"] == "KRX"
    assert conn.last_correlation_id == wire["intent_id"]
    assert result["status"] == "admitted"


def test_pika_intent_client_sell_preserves_venue():
    from trex_tvr.transport.intent_client import PikaIntentClient

    conn = _StubConnection({"intent_id": "sell-1", "status": "admitted", "slot_id": "slot-1"})
    client = PikaIntentClient(conn, instance_key="default")

    result = client.submit_sell({
        "intent_type": "sell",
        "intent_id": "sell-1",
        "slot_id": "slot-1",
        "qty": 1,
        "reason": "rebalance_sell",
        "sell_strategy_id": "tvr_v1",
        "venue": "NXT",
    })

    wire = json.loads(conn.last_body_bytes)
    assert wire["venue"] == "NXT"
    assert conn.last_correlation_id == wire["intent_id"]
    assert result["status"] == "admitted"
