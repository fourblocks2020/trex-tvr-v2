"""REQ-002 transport buy/sell-intent 경로 단위 테스트.

Tests:
  - build_buy_intent() 필드 구조 + venue
  - build_sell_intent() 필드 구조 + 멱등 intent_id
  - parse_sell_intent_response()
  - IntentPort Protocol (submit_buy + submit_sell)
  - qty 유효성 검사
"""
from __future__ import annotations

import pytest

from trex_tvr.runtime.intent_builder import build_buy_intent, build_sell_intent, make_sell_intent_id
from trex_tvr.strategies.base import BuySignal, SellSignal
from trex_tvr.transport.base import IntentPort, WireSchemaError, parse_sell_intent_response


# ---------------------------------------------------------------------------
def test_build_buy_intent_includes_required_venue():
    signal = BuySignal(
        symbol="005930",
        target_qty=3,
        allocated_capital=100000,
        reason="rebalance_buy",
        strategy_id="tvr_v1",
        entry_price=71000,
    )
    payload = build_buy_intent(
        signal,
        trading_date="2026-07-04",
        slot_id="slot-buy",
        stop_loss_pct=0.0,
        take_profit_pct=0.0,
        strategy_id="tvr_v1",
        venue="KRX",
    )

    assert payload["intent_type"] == "buy"
    assert payload["venue"] == "KRX"
    assert payload["slot_id"] == "slot-buy"
    assert payload["entry_price"] == 71000


# ---------------------------------------------------------------------------
# AC-REQ002-1: build_sell_intent 필드 구조 + 금지 필드 없음
# ---------------------------------------------------------------------------
def test_ac1_build_sell_intent_fields():
    """AC-REQ002-1: SellSignal → dict with correct fields, no stop_loss/take_profit."""
    signal = SellSignal(symbol="005930", slot_id="slot-abc", qty=4,
                        reason="rebalance_sell", strategy_id="tvr_v1")
    payload = build_sell_intent(
        signal,
        trading_date="2026-07-04",
        slot_id="slot-abc",
        strategy_id="tvr_v1",
        venue="KRX",
        decision_at="2026-07-04T10:00:00+09:00",
    )

    assert payload["intent_type"] == "sell"
    # BAT-002 REQ-007: intent_id now includes instance_key (default="default").
    assert payload["intent_id"] == (
        "sell:tvr_v1:default:2026-07-04:slot-abc:2026-07-04T10:00:00+09:00"
    )
    assert payload["slot_id"] == "slot-abc"
    assert payload["qty"] == 4
    assert payload["reason"] == "rebalance_sell"
    assert payload["sell_strategy_id"] == "tvr_v1"
    assert payload["venue"] == "KRX"
    # No exit fields (REQ-002: stop_loss_price / take_profit_price / forced_close_at absent)
    assert "stop_loss_price" not in payload
    assert "take_profit_price" not in payload
    assert "forced_close_at" not in payload


# ---------------------------------------------------------------------------
# AC-REQ002-2: 멱등 — 동일 입력 2회 호출 → 동일 intent_id
# ---------------------------------------------------------------------------
def test_ac2_sell_intent_id_is_stable_within_decision_and_changes_next_cycle():
    """One decision is idempotent; a later snapshot decision gets a fresh key."""
    signal = SellSignal(
        symbol="005930", slot_id="slot-abc", qty=4,
        reason="rebalance_sell", strategy_id="tvr_v1",
    )
    common = {
        "trading_date": "2026-07-04",
        "slot_id": "slot-abc",
        "strategy_id": "tvr_v1",
        "venue": "KRX",
    }
    id1 = build_sell_intent(
        signal, **common, decision_at="2026-07-04T10:00:00+09:00"
    )["intent_id"]
    id2 = build_sell_intent(
        signal, **common, decision_at="2026-07-04T10:00:00+09:00"
    )["intent_id"]
    id3 = build_sell_intent(
        signal, **common, decision_at="2026-07-04T10:01:00+09:00"
    )["intent_id"]
    assert id1 == id2
    assert id3 != id1


# ---------------------------------------------------------------------------
def test_ac3_intent_port_protocol_stub():
    """AC-REQ002-3/4: stub IntentPort with submit_buy + submit_sell passes isinstance check."""
    from tests.conftest import StubIntentPort

    client = StubIntentPort({"status": "admitted", "intent_id": "x"})
    assert isinstance(client, IntentPort)


# ---------------------------------------------------------------------------
# AC-REQ002-5: parse_sell_intent_response 정상 케이스
# ---------------------------------------------------------------------------
def test_ac5_parse_sell_response_ok():
    """AC-REQ002-5: valid sell response → normalized dict."""
    result = parse_sell_intent_response(
        {"intent_id": "x", "status": "admitted", "slot_id": "s1"}
    )
    assert result["intent_id"] == "x"
    assert result["status"] == "admitted"
    assert result["slot_id"] == "s1"
    assert result["reason"] is None


# ---------------------------------------------------------------------------
# AC-REQ002-6: parse_sell_intent_response — intent_id 없으면 WireSchemaError
# ---------------------------------------------------------------------------
def test_ac6_parse_sell_response_missing_intent_id():
    """AC-REQ002-6: sell response without intent_id → WireSchemaError."""
    with pytest.raises(WireSchemaError):
        parse_sell_intent_response({"status": "admitted"})


# ---------------------------------------------------------------------------
# AC-REQ002-8: qty <= 0 → ValueError
# ---------------------------------------------------------------------------
def test_ac8_build_sell_intent_invalid_qty():
    """AC-REQ002-8: qty=0 → ValueError."""
    signal = SellSignal(symbol="005930", slot_id="slot-1", qty=0,
                        reason="rebalance_sell", strategy_id="tvr_v1")
    with pytest.raises(ValueError, match="qty"):
        build_sell_intent(
            signal,
            trading_date="2026-07-04",
            slot_id="slot-1",
            strategy_id="tvr_v1",
            venue="KRX",
            decision_at="cycle-1",
        )


def test_ac8_build_sell_intent_negative_qty():
    """AC-REQ002-8: qty=-1 → ValueError."""
    signal = SellSignal(symbol="005930", slot_id="slot-1", qty=-1,
                        reason="rebalance_sell", strategy_id="tvr_v1")
    with pytest.raises(ValueError, match="qty"):
        build_sell_intent(
            signal,
            trading_date="2026-07-04",
            slot_id="slot-1",
            strategy_id="tvr_v1",
            venue="KRX",
            decision_at="cycle-1",
        )


# ---------------------------------------------------------------------------
# make_sell_intent_id: namespace prefix test
# ---------------------------------------------------------------------------
def test_sell_intent_id_namespace_prefix():
    """sell: prefix separates from buy intent_id namespace."""
    sell_id = make_sell_intent_id(
        "tvr_v1", "2026-07-04", "slot-1", decision_at="cycle-1"
    )
    assert sell_id.startswith("sell:")
    # BAT-002 REQ-007: intent_id now includes instance_key (default="default").
    assert sell_id == "sell:tvr_v1:default:2026-07-04:slot-1:cycle-1"


def test_sell_intent_id_requires_decision_cycle_token():
    with pytest.raises(ValueError, match="decision_at"):
        make_sell_intent_id("tvr_v1", "2026-07-04", "slot-1")


@pytest.mark.parametrize(
    ("builder", "kwargs"),
    [
        (
            build_buy_intent,
            {
                "signal": BuySignal(
                    symbol="005930",
                    target_qty=3,
                    allocated_capital=100000,
                    reason="rebalance_buy",
                    strategy_id="tvr_v1",
                    entry_price=71000,
                ),
                "trading_date": "2026-07-04",
                "slot_id": "slot-buy",
                "stop_loss_pct": 0.0,
                "take_profit_pct": 0.0,
                "strategy_id": "tvr_v1",
            },
        ),
        (
            build_sell_intent,
            {
                "signal": SellSignal(
                    symbol="005930",
                    slot_id="slot-sell",
                    qty=1,
                    reason="rebalance_sell",
                    strategy_id="tvr_v1",
                ),
                "trading_date": "2026-07-04",
                "slot_id": "slot-sell",
                "strategy_id": "tvr_v1",
                "decision_at": "cycle-1",
            },
        ),
    ],
)
def test_intent_builders_require_valid_venue(builder, kwargs):
    with pytest.raises(ValueError, match="venue"):
        builder(**kwargs)
    with pytest.raises(ValueError, match="venue"):
        builder(**{**kwargs, "venue": "SOR"})
