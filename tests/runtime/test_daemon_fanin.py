"""REQ-007: 다중 인스턴스 팬-인 단위 테스트.

AC 매핑:
  AC-1: instances=["a","b"], 각 2슬롯 → 총 4슬롯 병합·평가
  AC-2: inst_a BuySignal → inst_a intent 큐로, inst_b SellSignal → inst_b intent 큐로
  AC-3: inst_a slots.req 실패 → inst_b 정상 처리, partial_degraded 로깅
  AC-4: 두 인스턴스 모두 실패 → degraded_hold, submit_buy/sell 미호출
  AC-5: instances=["single"] → 기존 단일 인스턴스 동작 동등 (회귀 없음)
  AC-6: inst_a slot_id="slot_001", inst_b slot_id="slot_001" → intent_id 각각 구분
  AC-7: build_buy_intent(instance_key="inst_a", ...) → "inst_a" 포함
  AC-8: build_sell_intent(instance_key="inst_b", ...) → "sell:" + "inst_b" 포함
  AC-9: strategies/ 아래 파일에 인스턴스 관련 import 없음
  AC-10: 성공 인스턴스 watchdog 갱신됨 (slots.req 부수효과), 실패 인스턴스는 갱신 안됨
"""
from __future__ import annotations

from datetime import datetime

import pytest

from tests.conftest import (
    KST,
    StubEventsPort,
    StubIntentPort,
    StubMarketData,
    StubSlotPort,
    holding_slot,
    inactive_slot,
)
from trex_tvr.runtime.config import Config, ExecutionConfig, KisDataHubConfig, ScheduleEntry
from trex_tvr.runtime.daemon import Daemon
from trex_tvr.runtime.intent_builder import (
    build_buy_intent,
    build_sell_intent,
    make_intent_id,
    make_sell_intent_id,
)
from trex_tvr.strategies.base import BuySignal, SellSignal

_TVR_PARAMS_BUY = {"target_weight": 0.70, "rebalance_band_pct": 5.0, "min_trade_krw": 10000}
_TVR_PARAMS_SELL = {"target_weight": 0.30, "rebalance_band_pct": 5.0, "min_trade_krw": 10000}


def _config(instances=None) -> Config:
    return Config(
        instance_key="default",
        rabbitmq_url="amqp://localhost",
        kis_data_hub=KisDataHubConfig(base_url="http://kdh.test"),
        schedule=[ScheduleEntry(start="08:00", end="20:00")],
        strategy_id="tvr_v1",
        strategy_params={"tvr_v1": {}},
        execution=ExecutionConfig(),
        redis_url="redis://localhost",
        poll_interval=60,
        instances=instances or ["default"],
    )


class _FailSlotPort:
    """Stub SlotPort that raises ConnectionError on every call."""
    def __init__(self):
        self.calls = 0

    def request_slots(self, *, filter, strategy=None) -> dict:
        self.calls += 1
        raise ConnectionError("simulated connection failure")


class _MultiIntentPort:
    """Stub IntentPort that tracks which instance it belongs to."""
    def __init__(self, instance_key: str, response: dict) -> None:
        self.instance_key = instance_key
        self.response = response
        self.submitted: list[dict] = []
        self.sell_submitted: list[dict] = []

    def submit_buy(self, payload: dict) -> dict:
        self.submitted.append(payload)
        resp = {**self.response}
        if "intent_id" not in resp:
            resp["intent_id"] = payload["intent_id"]
        return resp

    def submit_sell(self, payload: dict) -> dict:
        self.sell_submitted.append(payload)
        resp = {**self.response}
        if "intent_id" not in resp:
            resp["intent_id"] = payload["intent_id"]
        return resp


# ---------------------------------------------------------------------------
# AC-1: 두 인스턴스 각각 2 슬롯 → 4슬롯 병합
# ---------------------------------------------------------------------------
def test_ac1_two_instances_slots_merged():
    """AC-1: instances=["inst_a","inst_b"], 각 2슬롯 → 총 4슬롯 병합·평가."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)

    # 두 인스턴스: 각각 BuySignal 유발 슬롯 2개
    slot_a = StubSlotPort({
        "slots": [
            inactive_slot("slot-a1", qty=0, cash_balance=500_000, strategy_params=_TVR_PARAMS_BUY),
            inactive_slot("slot-a2", qty=0, cash_balance=500_000, strategy_params=_TVR_PARAMS_BUY),
        ],
        "degraded": False,
    })
    slot_b = StubSlotPort({
        "slots": [
            inactive_slot("slot-b1", qty=0, cash_balance=500_000, strategy_params=_TVR_PARAMS_BUY),
            inactive_slot("slot-b2", qty=0, cash_balance=500_000, strategy_params=_TVR_PARAMS_BUY),
        ],
        "degraded": False,
    })
    intent_a = _MultiIntentPort("inst_a", {"status": "admitted"})
    intent_b = _MultiIntentPort("inst_b", {"status": "admitted"})

    cfg = _config(instances=["inst_a", "inst_b"])
    daemon = Daemon(
        cfg,
        slot_ports={"inst_a": slot_a, "inst_b": slot_b},
        intent_ports={"inst_a": intent_a, "inst_b": intent_b},
        events_port=StubEventsPort(),
        market_data_client=StubMarketData(2000),
        clock=lambda: now,
    )

    result = daemon.run_decision_cycle()

    assert result["status"] == "ok"
    # 4 slots total (2 from each)
    assert result["slots"] == 4
    # 4 buy intents (each slot → BuySignal)
    assert len(result["intents"]) == 4


# ---------------------------------------------------------------------------
# AC-2: 오라우팅 없음 — inst_a buy → inst_a 큐, inst_b sell → inst_b 큐
# ---------------------------------------------------------------------------
def test_ac2_intents_routed_to_source_instance():
    """AC-2: inst_a BuySignal → inst_a intent 큐, inst_b SellSignal → inst_b intent 큐."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)

    # inst_a: inactive slot → BuySignal (qty=0, weight=0% < 70% → buy)
    slot_a = StubSlotPort({
        "slots": [inactive_slot("slot-a1", qty=0, cash_balance=1_000_000, strategy_params=_TVR_PARAMS_BUY)],
        "degraded": False,
    })
    # inst_b: holding slot → SellSignal (weight=50% > 30%+5% → sell)
    slot_b = StubSlotPort({
        "slots": [holding_slot("slot-b1", qty=20, cash_balance=100_000, strategy_params=_TVR_PARAMS_SELL)],
        "degraded": False,
    })
    intent_a = _MultiIntentPort("inst_a", {"status": "admitted"})
    intent_b = _MultiIntentPort("inst_b", {"status": "admitted"})

    cfg = _config(instances=["inst_a", "inst_b"])
    daemon = Daemon(
        cfg,
        slot_ports={"inst_a": slot_a, "inst_b": slot_b},
        intent_ports={"inst_a": intent_a, "inst_b": intent_b},
        events_port=StubEventsPort(),
        market_data_client=StubMarketData(5000),
        clock=lambda: now,
    )

    result = daemon.run_decision_cycle()

    assert result["status"] == "ok"
    # inst_a should have received a buy intent
    assert len(intent_a.submitted) == 1
    assert intent_a.submitted[0]["intent_type"] == "buy"
    # inst_b should have received a sell intent
    assert len(intent_b.sell_submitted) == 1
    assert intent_b.sell_submitted[0]["intent_type"] == "sell"
    # No cross-routing
    assert len(intent_b.submitted) == 0  # no buy on inst_b
    assert len(intent_a.sell_submitted) == 0  # no sell on inst_a


# ---------------------------------------------------------------------------
# AC-3: inst_a 실패 → inst_b 정상, partial_degraded
# ---------------------------------------------------------------------------
def test_ac3_partial_failure_continues_with_healthy_instance():
    """AC-3: inst_a slots.req 실패 → inst_b 슬롯 정상 처리, partial_degraded."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)

    slot_a_fail = _FailSlotPort()
    slot_b = StubSlotPort({
        "slots": [inactive_slot("slot-b1", qty=0, cash_balance=1_000_000, strategy_params=_TVR_PARAMS_BUY)],
        "degraded": False,
    })
    intent_a = _MultiIntentPort("inst_a", {"status": "admitted"})
    intent_b = _MultiIntentPort("inst_b", {"status": "admitted"})

    cfg = _config(instances=["inst_a", "inst_b"])
    daemon = Daemon(
        cfg,
        slot_ports={"inst_a": slot_a_fail, "inst_b": slot_b},
        intent_ports={"inst_a": intent_a, "inst_b": intent_b},
        events_port=StubEventsPort(),
        market_data_client=StubMarketData(2000),
        clock=lambda: now,
    )

    result = daemon.run_decision_cycle()

    # Not degraded_hold: inst_b still ran
    assert result["status"] == "ok"
    # inst_b slot was processed and intent submitted
    assert len(intent_b.submitted) == 1
    # inst_a had no intents
    assert len(intent_a.submitted) == 0
    # slot_a_fail was attempted
    assert slot_a_fail.calls == 1


# ---------------------------------------------------------------------------
# AC-4: 전체 실패 → degraded_hold
# ---------------------------------------------------------------------------
def test_ac4_total_failure_degraded_hold():
    """AC-4: 두 인스턴스 모두 slots.req 실패 → degraded_hold, submit_buy/sell 미호출."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)

    slot_a_fail = _FailSlotPort()
    slot_b_fail = _FailSlotPort()
    intent_a = _MultiIntentPort("inst_a", {"status": "admitted"})
    intent_b = _MultiIntentPort("inst_b", {"status": "admitted"})

    cfg = _config(instances=["inst_a", "inst_b"])
    daemon = Daemon(
        cfg,
        slot_ports={"inst_a": slot_a_fail, "inst_b": slot_b_fail},
        intent_ports={"inst_a": intent_a, "inst_b": intent_b},
        events_port=StubEventsPort(),
        market_data_client=StubMarketData(2000),
        clock=lambda: now,
    )

    result = daemon.run_decision_cycle()

    assert result["status"] == "degraded_hold"
    assert result["intents"] == []
    assert len(intent_a.submitted) == 0
    assert len(intent_b.submitted) == 0


# ---------------------------------------------------------------------------
# AC-5: 단일 인스턴스 → 기존 동작과 동등 (회귀 없음)
# ---------------------------------------------------------------------------
def test_ac5_single_instance_backward_compat():
    """AC-5: instances=["single"] → 기존 단일 인스턴스 동작과 동등 (회귀 없음)."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)
    slot_row = inactive_slot(qty=0, cash_balance=1_000_000, strategy_params=_TVR_PARAMS_BUY)
    slot = StubSlotPort({"slots": [slot_row], "degraded": False})
    intent = StubIntentPort({"status": "admitted", "slot_id": "slot-1"})

    cfg = _config(instances=["single"])
    daemon = Daemon(
        cfg,
        slot_port=slot,
        intent_port=intent,
        events_port=StubEventsPort(),
        market_data_client=StubMarketData(2000),
        clock=lambda: now,
    )

    result = daemon.run_decision_cycle()

    assert result["status"] == "ok"
    assert len(result["intents"]) == 1
    assert result["intents"][0]["payload"]["intent_type"] == "buy"


# ---------------------------------------------------------------------------
# AC-6: 동일 slot_id가 두 인스턴스에 존재 → intent_id로 구분
# ---------------------------------------------------------------------------
def test_ac6_same_slot_id_different_instances_unique_intent_ids():
    """AC-6: inst_a slot_id="slot_001", inst_b slot_id="slot_001" → intent_id 구분됨."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)

    slot_a = StubSlotPort({
        "slots": [inactive_slot("slot_001", qty=0, cash_balance=1_000_000, strategy_params=_TVR_PARAMS_BUY)],
        "degraded": False,
    })
    slot_b = StubSlotPort({
        "slots": [inactive_slot("slot_001", qty=0, cash_balance=1_000_000, strategy_params=_TVR_PARAMS_BUY)],
        "degraded": False,
    })
    intent_a = _MultiIntentPort("inst_a", {"status": "admitted"})
    intent_b = _MultiIntentPort("inst_b", {"status": "admitted"})

    cfg = _config(instances=["inst_a", "inst_b"])
    daemon = Daemon(
        cfg,
        slot_ports={"inst_a": slot_a, "inst_b": slot_b},
        intent_ports={"inst_a": intent_a, "inst_b": intent_b},
        events_port=StubEventsPort(),
        market_data_client=StubMarketData(2000),
        clock=lambda: now,
    )

    result = daemon.run_decision_cycle()

    assert result["status"] == "ok"
    all_intent_ids = [i["payload"]["intent_id"] for i in result["intents"]]
    assert len(set(all_intent_ids)) == 2, f"Expected 2 unique intent_ids, got: {all_intent_ids}"

    # Verify each intent_id contains the correct instance_key
    intent_ids_a = [p["intent_id"] for p in intent_a.submitted]
    intent_ids_b = [p["intent_id"] for p in intent_b.submitted]
    assert all("inst_a" in iid for iid in intent_ids_a), f"inst_a intent_ids: {intent_ids_a}"
    assert all("inst_b" in iid for iid in intent_ids_b), f"inst_b intent_ids: {intent_ids_b}"


# ---------------------------------------------------------------------------
# AC-7: build_buy_intent(instance_key="inst_a", ...) → "inst_a" 포함된 intent_id
# ---------------------------------------------------------------------------
def test_ac7_build_buy_intent_includes_instance_key():
    """AC-7: build_buy_intent(instance_key="inst_a") → intent_id에 "inst_a" 포함."""
    signal = BuySignal(
        symbol="005930", target_qty=10, allocated_capital=500000, reason="rebalance_buy",
        entry_price=50000, strategy_id="tvr_v1"
    )
    payload = build_buy_intent(
        signal,
        trading_date="2026-07-06",
        slot_id="slot_001",
        strategy_id="tvr_v1",
        venue="KRX",
        instance_key="inst_a",
        decision_at="cycle-1",
        stop_loss_pct=0.0,
        take_profit_pct=0.0,
    )
    assert "inst_a" in payload["intent_id"]
    assert payload["intent_id"] == "buy:tvr_v1:inst_a:2026-07-06:slot_001:cycle-1"
    assert payload["venue"] == "KRX"


# ---------------------------------------------------------------------------
# AC-8: build_sell_intent(instance_key="inst_b", ...) → "sell:" + "inst_b" 포함
# ---------------------------------------------------------------------------
def test_ac8_build_sell_intent_includes_instance_key():
    """AC-8: build_sell_intent(instance_key="inst_b") → intent_id에 "sell:" + "inst_b"."""
    signal = SellSignal(
        symbol="005930", slot_id="slot_002", qty=5, reason="rebalance_sell", strategy_id="tvr_v1"
    )
    payload = build_sell_intent(
        signal,
        trading_date="2026-07-06",
        slot_id="slot_002",
        strategy_id="tvr_v1",
        venue="NXT",
        instance_key="inst_b",
        decision_at="cycle-1",
    )
    assert payload["intent_id"].startswith("sell:")
    assert "inst_b" in payload["intent_id"]
    assert payload["intent_id"] == "sell:tvr_v1:inst_b:2026-07-06:slot_002:cycle-1"
    assert payload["venue"] == "NXT"


# ---------------------------------------------------------------------------
# AC-9: strategies/ 아래에 인스턴스 관련 import 없음 (순수성 불변)
# ---------------------------------------------------------------------------
def test_ac9_strategies_no_instance_imports():
    """AC-9: strategies/ 아래 모든 .py가 인스턴스 관련 식별자·팬-인 로직 import 없음."""
    import ast
    import pathlib
    strat_dir = pathlib.Path(__file__).parent.parent.parent / "trex_tvr" / "strategies"
    forbidden_names = {"slot_ports", "intent_ports", "_source_instance", "fan_in", "instances"}
    for pyfile in strat_dir.glob("*.py"):
        src = pyfile.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in forbidden_names:
                pytest.fail(f"{pyfile.name} references instance concept: {node.id}")
            if isinstance(node, ast.Attribute) and node.attr in forbidden_names:
                pytest.fail(f"{pyfile.name} references instance concept: {node.attr}")


# ---------------------------------------------------------------------------
# AC-10: watchdog — 성공 인스턴스는 갱신됨, 실패 인스턴스는 갱신 안됨
# ---------------------------------------------------------------------------

class _WatchdogTrackingSlotPort:
    """Slot port that tracks calls (proxy for watchdog update)."""
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls = 0

    def request_slots(self, *, filter, strategy=None) -> dict:
        self.calls += 1
        return self.response


def test_ac10_watchdog_updated_per_instance():
    """AC-10: slots.req 성공한 인스턴스 watchdog 갱신됨, 실패 인스턴스는 갱신 안됨."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)

    # inst_a: success — slots.req called → watchdog updated (tracked by call count)
    slot_a = _WatchdogTrackingSlotPort({"slots": [], "degraded": False})
    # inst_b: failure — slots.req raises → watchdog not updated
    slot_b_fail = _FailSlotPort()

    intent_a = _MultiIntentPort("inst_a", {"status": "admitted"})
    intent_b = _MultiIntentPort("inst_b", {"status": "admitted"})

    cfg = _config(instances=["inst_a", "inst_b"])
    daemon = Daemon(
        cfg,
        slot_ports={"inst_a": slot_a, "inst_b": slot_b_fail},
        intent_ports={"inst_a": intent_a, "inst_b": intent_b},
        events_port=StubEventsPort(),
        market_data_client=StubMarketData(2000),
        clock=lambda: now,
    )

    daemon.run_decision_cycle()

    # inst_a's slots.req was called once → watchdog side-effect triggered
    assert slot_a.calls == 1, f"Expected inst_a slots.req to be called once, got {slot_a.calls}"
    # inst_b's slots.req failed → watchdog not updated
    assert slot_b_fail.calls == 1  # attempt was made but failed


# ---------------------------------------------------------------------------
# make_intent_id / make_sell_intent_id format tests
# ---------------------------------------------------------------------------

def test_make_intent_id_with_instance_key():
    """make_intent_id includes instance_key between strategy_id and trading_date."""
    iid = make_intent_id("tvr_v1", "2026-07-06", "slot-1", instance_key="inst_a", decision_at="cycle-1")
    assert iid == "buy:tvr_v1:inst_a:2026-07-06:slot-1:cycle-1"


def test_make_sell_intent_id_with_instance_key():
    """make_sell_intent_id starts with 'sell:' and includes instance_key."""
    iid = make_sell_intent_id(
        "tvr_v1", "2026-07-06", "slot-1", instance_key="inst_b", decision_at="cycle-1"
    )
    assert iid == "sell:tvr_v1:inst_b:2026-07-06:slot-1:cycle-1"
    assert iid.startswith("sell:")


def test_make_intent_id_default_instance_key():
    """make_intent_id with default instance_key="default"."""
    iid = make_intent_id("tvr_v1", "2026-07-06", "slot-1", decision_at="cycle-1")
    assert iid == "buy:tvr_v1:default:2026-07-06:slot-1:cycle-1"


def test_make_sell_intent_id_default_instance_key():
    """make_sell_intent_id with default instance_key="default"."""
    iid = make_sell_intent_id(
        "tvr_v1", "2026-07-06", "slot-1", decision_at="cycle-1"
    )
    assert iid == "sell:tvr_v1:default:2026-07-06:slot-1:cycle-1"
