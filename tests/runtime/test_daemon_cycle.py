"""One full decision cycle with stub transport + stub market data (engine smoke)."""
from __future__ import annotations

from datetime import datetime

from tests.conftest import (
    KST,
    StubEventsPort,
    StubIntentPort,
    StubMarketData,
    StubSlotPort,
    inactive_slot,
)
from trex_tvr.runtime.config import (
    Config,
    ExecutionConfig,
    ExitConfig,
    KisDataHubConfig,
    ScheduleEntry,
)
from trex_tvr.runtime.daemon import Daemon

_STRATEGY_PARAMS = {"tvr_v1": {"min_price": 1000}}


def _config() -> Config:
    return Config(
        instance_key="default",
        rabbitmq_url="amqp://localhost",
        kis_data_hub=KisDataHubConfig(base_url="http://kdh.test"),
        schedule=[ScheduleEntry(start="09:15", end="15:20")],
        exit=ExitConfig(stop_loss_pct=2.0, take_profit_pct=4.0),
        strategy_id="tvr_v1",
        entry_capital_krw=1_000_000,
        strategy_params=_STRATEGY_PARAMS,
        execution=ExecutionConfig(),
    )


def _daemon(slot, intent, market_data, now):
    return Daemon(
        _config(),
        slot_port=slot,
        intent_port=intent,
        events_port=StubEventsPort(),
        market_data_client=market_data,
        clock=lambda: now,
    )


def test_full_cycle_emits_slot_id_keyed_buy_intent():
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)
    slot = StubSlotPort({"slots": [inactive_slot()], "degraded": False})
    intent = StubIntentPort({"status": "admitted", "slot_id": "slot-1"})
    daemon = _daemon(slot, intent, StubMarketData(2000), now)

    result = daemon.run_decision_cycle()

    assert result["status"] == "ok"
    assert len(result["intents"]) == 1
    payload = result["intents"][0]["payload"]
    assert payload["intent_type"] == "buy"
    assert payload["slot_id"] == "slot-1"
    assert payload["intent_id"] == "tvr_v1:2026-06-08:slot-1"
    assert slot.calls == [{"filter": "inactive"}]


def test_out_of_schedule_skips():
    now = datetime(2026, 6, 8, 8, 0, tzinfo=KST)  # before 09:15
    slot = StubSlotPort({"slots": [inactive_slot()]})
    daemon = _daemon(slot, StubIntentPort({"status": "admitted"}), StubMarketData(2000), now)
    result = daemon.run_decision_cycle()
    assert result["status"] == "skipped" and result["reason"] == "out_of_schedule"
    assert slot.calls == []
