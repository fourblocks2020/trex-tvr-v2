"""TVR rebalancing decision cycle — engine smoke tests (stub transport + stub market data).

BAT-001: Updated from scaffold placeholder to rebalancing-type engine tests.
- entry_capital_krw removed (TVR uses strategy_params.target_weight)
- [exit] is optional (TVR = pure rebalancing, no stop_loss/take_profit on buy intent)
- strategy_params injected via slot row
- inactive + holding slots both evaluated
"""
from __future__ import annotations

from datetime import datetime

from tests.conftest import (
    KST,
    StubEventsPort,
    StubIntentPort,
    StubMarketData,
    StubSlotPort,
    inactive_slot,
    holding_slot,
)
from trex_tvr.runtime.config import (
    Config,
    ExecutionConfig,
    KisDataHubConfig,
    ScheduleEntry,
)
from trex_tvr.runtime.daemon import Daemon

# strategy_params with target_weight=0.70 — current_price=2000, qty=10, cash=500_000:
# stock_value=20_000, total=520_000, weight=3.8% < 70% → BuySignal
_STRATEGY_PARAMS_TVR = {
    "tvr_v1": {}  # per-slot params come from slot row, not config
}

# Slot row with strategy_params for a slot that needs rebalancing (buy case)
_TVR_PARAMS = {"target_weight": 0.70, "rebalance_band_pct": 5.0, "min_trade_krw": 10000}


def _config() -> Config:
    return Config(
        instance_key="default",
        rabbitmq_url="amqp://localhost",
        kis_data_hub=KisDataHubConfig(base_url="http://kdh.test"),
        schedule=[ScheduleEntry(start="09:15", end="15:20")],
        strategy_id="tvr_v1",
        # exit=None: TVR does not use exit thresholds
        strategy_params=_STRATEGY_PARAMS_TVR,
        execution=ExecutionConfig(),
        redis_url="redis://localhost",
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


def test_full_cycle_emits_buy_intent_for_inactive_slot():
    """AC-REQ003-2: inactive slot + BuySignal → submit_buy called, no stop_loss/take_profit."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)
    # qty=0, cash=1_000_000 → weight=0% < 70%-5%=65% → BuySignal
    slot_row = inactive_slot(
        qty=0,
        cash_balance=1_000_000,
        strategy_params=_TVR_PARAMS,
    )
    slot = StubSlotPort({"slots": [slot_row], "degraded": False})
    intent = StubIntentPort({"status": "admitted", "slot_id": "slot-1"})
    daemon = _daemon(slot, intent, StubMarketData(2000), now)

    result = daemon.run_decision_cycle()

    assert result["status"] == "ok"
    assert len(result["intents"]) == 1
    payload = result["intents"][0]["payload"]
    assert payload["intent_type"] == "buy"
    assert payload["slot_id"] == "slot-1"
    assert payload["intent_id"] == "tvr_v1:2026-06-08:slot-1"
    # REQ-003 AC-2: TVR buy intent has no stop_loss_price / take_profit_price
    assert "stop_loss_price" not in payload
    assert "take_profit_price" not in payload


def test_full_cycle_emits_sell_intent_for_holding_slot():
    """AC-REQ003-3: holding slot + SellSignal → submit_sell called."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)
    # qty=20, cash=100_000, price=2000 → stock=40_000, total=140_000, weight≈28.6% > 70%?
    # target=0.30, band=5%: weight=28.6% < target=30% → buy? Let's use a clear sell case:
    # qty=20, cash=100_000, price=5000 → stock=100_000, total=200_000, weight=50% > 30%+5% → sell
    params = {"target_weight": 0.30, "rebalance_band_pct": 5.0, "min_trade_krw": 10000}
    slot_row = holding_slot(
        qty=20,
        cash_balance=100_000,
        strategy_params=params,
    )
    slot = StubSlotPort({"slots": [slot_row], "degraded": False})
    intent = StubIntentPort({"status": "admitted", "slot_id": "slot-1"})
    daemon = _daemon(slot, intent, StubMarketData(5000), now)

    result = daemon.run_decision_cycle()

    assert result["status"] == "ok"
    sell_intents = [i for i in result["intents"] if i["payload"].get("intent_type") == "sell"]
    assert len(sell_intents) == 1
    payload = sell_intents[0]["payload"]
    assert payload["slot_id"] == "slot-1"
    assert payload["intent_id"].startswith("sell:tvr_v1:")
    assert payload["qty"] > 0


def test_out_of_schedule_skips():
    """Cycle skips when current time is outside schedule window."""
    now = datetime(2026, 6, 8, 8, 0, tzinfo=KST)  # before 09:15
    slot = StubSlotPort({"slots": [inactive_slot()]})
    daemon = _daemon(slot, StubIntentPort({"status": "admitted"}), StubMarketData(2000), now)
    result = daemon.run_decision_cycle()
    assert result["status"] == "skipped" and result["reason"] == "out_of_schedule"
    assert slot.calls == []


def test_missing_strategy_params_skips_slot():
    """AC-REQ003-4: slot without strategy_params → missing_strategy_params diagnostic, no intent."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)
    slot_row = inactive_slot()  # no strategy_params key
    slot = StubSlotPort({"slots": [slot_row], "degraded": False})
    intent = StubIntentPort({"status": "admitted"})
    daemon = _daemon(slot, intent, StubMarketData(2000), now)

    result = daemon.run_decision_cycle()

    assert result["status"] == "no_op"
    assert len(result["intents"]) == 0
    diag_reasons = [d["reason"] for d in result["diagnostics"]]
    assert "missing_strategy_params" in diag_reasons


def test_already_active_buy_recorded_as_diagnostic():
    """AC-REQ003-5: submit_buy returns already_active → diagnostics, no exception."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)
    slot_row = inactive_slot(qty=0, cash_balance=1_000_000, strategy_params=_TVR_PARAMS)
    slot = StubSlotPort({"slots": [slot_row], "degraded": False})
    intent = StubIntentPort({"status": "rejected", "reason": "already_active", "slot_id": "slot-1"})
    daemon = _daemon(slot, intent, StubMarketData(2000), now)

    result = daemon.run_decision_cycle()

    assert result["status"] == "ok"
    diag_reasons = [d["reason"] for d in result["diagnostics"]]
    assert "already_active" in diag_reasons


def test_already_active_sell_recorded_as_diagnostic():
    """AC-REQ003-6: submit_sell returns already_active → diagnostics, no exception."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)
    # holding slot that would generate SellSignal
    params = {"target_weight": 0.30, "rebalance_band_pct": 5.0, "min_trade_krw": 10000}
    slot_row = holding_slot(qty=20, cash_balance=100_000, strategy_params=params)
    slot = StubSlotPort({"slots": [slot_row], "degraded": False})
    intent = StubIntentPort({"status": "rejected", "reason": "already_active", "slot_id": "slot-1"})
    daemon = _daemon(slot, intent, StubMarketData(5000), now)

    result = daemon.run_decision_cycle()

    assert result["status"] == "ok"
    diag_reasons = [d["reason"] for d in result["diagnostics"]]
    assert "already_active" in diag_reasons


def test_degraded_hold():
    """AC-REQ003-7: degraded slots response → degraded_hold, no intents."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)
    slot = StubSlotPort({"slots": [], "degraded": True, "reason": "test_degraded"})
    intent = StubIntentPort({"status": "admitted"})
    daemon = _daemon(slot, intent, StubMarketData(2000), now)

    result = daemon.run_decision_cycle()

    assert result["status"] == "degraded_hold"
    assert len(result["intents"]) == 0


def test_buy_reentry_guard_applies_to_buy_only():
    """AC-REQ003-8: buy reentry guard — same slot buy skipped on same day, holding sell not guarded."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)
    slot_row = inactive_slot(qty=0, cash_balance=1_000_000, strategy_params=_TVR_PARAMS)
    slot = StubSlotPort({"slots": [slot_row], "degraded": False})
    intent = StubIntentPort({"status": "admitted", "slot_id": "slot-1"})
    daemon = _daemon(slot, intent, StubMarketData(2000), now)

    # First cycle: buy admitted
    result1 = daemon.run_decision_cycle()
    assert len(result1["intents"]) == 1
    assert result1["intents"][0]["payload"]["intent_type"] == "buy"

    # Second cycle same day: buy reentry skip
    result2 = daemon.run_decision_cycle()
    assert len(result2["intents"]) == 0
    diag_reasons = [d.get("reason") for d in result2["diagnostics"]]
    assert "reentry_skip" in diag_reasons
