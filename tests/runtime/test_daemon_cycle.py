"""TVR rebalancing decision cycle — engine smoke tests (stub transport + stub market data).

BAT-001: Updated from scaffold placeholder to rebalancing-type engine tests.
- entry_capital_krw removed (TVR uses strategy_params.target_weight)
- [exit] is optional (TVR = pure rebalancing, no stop_loss/take_profit on buy intent)
- strategy_params injected via slot row
- inactive + holding slots both evaluated

BAT-002: Extended with session gate + NXT markets gate tests (REQ-005 AC-13, AC-14, AC-15).
"""
from __future__ import annotations

from datetime import datetime

import httpx

from tests.conftest import (
    KST,
    StubEventsPort,
    StubIntentPort,
    StubMarketData,
    StubSlotPort,
    holding_slot,
    inactive_slot,
)
from trex_tvr.market_data.kis_data_hub_client import KisDataHubClient
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


def _build_recording_kis_transport(calls: list[tuple[str, str, dict]], *, candles: list[dict] | None = None) -> httpx.MockTransport:
    """Return a mock transport that records endpoint calls and returns stable fixtures."""

    bars = candles if candles is not None else []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path == "/v1/quotes/latest" and request.method == "POST":
            return httpx.Response(200, json={"price": "2000"})
        if request.url.path == "/v1/candles/minute" and request.method == "GET":
            return httpx.Response(200, json=bars)
        return httpx.Response(404, json={"message": "unexpected path"})

    return httpx.MockTransport(_handler)


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
    assert "venue" not in payload
    # BAT-002 REQ-007: intent_id now includes instance_key.
    assert payload["intent_id"] == "tvr_v1:default:2026-06-08:slot-1"
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


def test_later_holding_snapshot_creates_independent_sell_without_broker_retry_input():
    """Each snapshot cycle is independent; broker events/retryability are not consumed."""
    params = {"target_weight": 0.30, "rebalance_band_pct": 5.0, "min_trade_krw": 10000}
    slot = StubSlotPort({
        "slots": [holding_slot(qty=20, cash_balance=100_000, strategy_params=params)],
        "degraded": False,
    })
    intent = StubIntentPort({"status": "rejected", "reason": "broker_rejected"})

    class EventsMustNotBePolled:
        def poll_events(self, max_messages=100):
            raise AssertionError("snapshot-only daemon must not consume broker events")

    daemon = Daemon(
        _config(),
        slot_port=slot,
        intent_port=intent,
        events_port=EventsMustNotBePolled(),
        market_data_client=StubMarketData(5000),
    )

    first = daemon.run_decision_cycle(datetime(2026, 6, 8, 10, 0, tzinfo=KST))
    second = daemon.run_decision_cycle(datetime(2026, 6, 8, 10, 1, tzinfo=KST))

    first_id = first["intents"][0]["payload"]["intent_id"]
    second_id = second["intents"][0]["payload"]["intent_id"]
    assert first_id != second_id
    assert "venue" not in first["intents"][0]["payload"]
    assert "venue" not in second["intents"][0]["payload"]


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


def test_req003_ac1_strategy_filter_passed_to_slot_port():
    """REQ-003 AC-1: strategy='tvr_v1' is forwarded to SlotPort.request_slots() on every cycle.

    QC MAJOR fix: daemon previously called request_slots(filter=...) only, dropping the
    strategy field. After the fix, slot.calls must contain strategy='tvr_v1'.
    """
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)
    slot_row = inactive_slot(qty=0, cash_balance=1_000_000, strategy_params=_TVR_PARAMS)
    slot = StubSlotPort({"slots": [slot_row], "degraded": False})
    intent = StubIntentPort({"status": "admitted", "slot_id": "slot-1"})
    daemon = _daemon(slot, intent, StubMarketData(2000), now)

    daemon.run_decision_cycle()

    assert len(slot.calls) == 1
    call = slot.calls[0]
    assert call["filter"] == "inactive,holding", "filter must be 'inactive,holding'"
    assert call["strategy"] == "tvr_v1", (
        f"strategy must be 'tvr_v1' in wire body; got {call['strategy']!r}"
    )


# ---------------------------------------------------------------------------
# BAT-002 REQ-005: session gate + NXT markets gate (AC-13, AC-14, AC-15)
# ---------------------------------------------------------------------------

class _HolidayCacheAlwaysHoliday:
    def is_trading_day(self, date_str: str) -> bool:
        return False


class _HolidayCacheAlwaysTrading:
    def is_trading_day(self, date_str: str) -> bool:
        return True


def _daemon_with_holiday(slot, intent, market_data, now, holiday_cache=None):
    from trex_tvr.runtime.config import Config, ExecutionConfig, KisDataHubConfig, ScheduleEntry
    from trex_tvr.runtime.daemon import Daemon
    cfg = Config(
        instance_key="default",
        rabbitmq_url="amqp://localhost",
        kis_data_hub=KisDataHubConfig(base_url="http://kdh.test"),
        schedule=[ScheduleEntry(start="08:00", end="20:00")],
        strategy_id="tvr_v1",
        strategy_params={"tvr_v1": {}},
        execution=ExecutionConfig(),
        redis_url="redis://localhost",
        poll_interval=60,
    )
    return Daemon(
        cfg,
        slot_port=slot,
        intent_port=intent,
        events_port=StubEventsPort(),
        market_data_client=market_data,
        clock=lambda: now,
        holiday_cache=holiday_cache,
    )


def test_ac13_session_hold_on_non_tradeable_session():
    """REQ-005 AC-13: is_tradeable=False → session_hold, 전략 평가 미수행, submit_buy/sell 미호출."""
    # CLOSE_AUCTION (15:20 ~ 15:30) is not tradeable
    now = datetime(2026, 6, 8, 15, 25, tzinfo=KST)
    slot_row = inactive_slot(qty=0, cash_balance=1_000_000, strategy_params=_TVR_PARAMS)
    slot = StubSlotPort({"slots": [slot_row], "degraded": False})
    intent = StubIntentPort({"status": "admitted", "slot_id": "slot-1"})
    daemon = _daemon_with_holiday(slot, intent, StubMarketData(2000), now, holiday_cache=_HolidayCacheAlwaysTrading())

    result = daemon.run_decision_cycle()

    assert result["status"] == "session_hold"
    # Strategy not evaluated — no slots request, no intents
    assert result["intents"] == []
    assert slot.calls == [], f"slots.req should not be called during session_hold, got {slot.calls}"
    assert intent.submitted == [], "submit_buy should not be called during session_hold"
    assert intent.sell_submitted == [], "submit_sell should not be called during session_hold"


def test_ac13_holiday_is_session_hold():
    """REQ-005 AC-13b: 휴장일 → session_hold (is_tradeable=False 전 구간)."""
    # MAIN session but holiday
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)
    slot_row = inactive_slot(qty=0, cash_balance=1_000_000, strategy_params=_TVR_PARAMS)
    slot = StubSlotPort({"slots": [slot_row], "degraded": False})
    intent = StubIntentPort({"status": "admitted", "slot_id": "slot-1"})
    daemon = _daemon_with_holiday(slot, intent, StubMarketData(2000), now, holiday_cache=_HolidayCacheAlwaysHoliday())

    result = daemon.run_decision_cycle()

    assert result["status"] == "session_hold"
    assert result["intents"] == []


def test_ac14_nxt_session_with_nxt_market_allows_intent():
    """REQ-005 AC-14: NXT 전용 구간 + markets=["NXT","KRX"] → 정상 평가 + intent 발신 허용."""
    # AFTER_MARKET (16:00) is NXT-only
    now = datetime(2026, 6, 8, 16, 0, tzinfo=KST)
    # Slot with NXT market — should proceed normally
    slot_row = {
        "slot_id": "slot-1", "status": "inactive", "symbol": "005930",
        "qty": 0, "cash_balance": 1_000_000, "strategy_params": _TVR_PARAMS,
        "markets": ["NXT", "KRX"],
    }
    slot = StubSlotPort({"slots": [slot_row], "degraded": False})
    intent = StubIntentPort({"status": "admitted", "slot_id": "slot-1"})
    daemon = _daemon_with_holiday(slot, intent, StubMarketData(2000), now, holiday_cache=_HolidayCacheAlwaysTrading())

    result = daemon.run_decision_cycle()

    assert result["status"] == "ok"
    # Should have submitted a buy intent
    assert len(result["intents"]) == 1
    assert result["intents"][0]["payload"]["intent_type"] == "buy"


def test_ac15_nxt_session_without_nxt_market_suppresses_intent():
    """REQ-005 AC-15: NXT 전용 구간 + markets=["KRX"] → intent 억제, Diagnostic(market_not_eligible)."""
    # AFTER_MARKET (16:00) is NXT-only
    now = datetime(2026, 6, 8, 16, 0, tzinfo=KST)
    # Slot with KRX-only market — should be suppressed in NXT session
    slot_row_krx = {
        "slot_id": "slot-1", "status": "inactive", "symbol": "005930",
        "qty": 0, "cash_balance": 1_000_000, "strategy_params": _TVR_PARAMS,
        "markets": ["KRX"],
    }
    slot = StubSlotPort({"slots": [slot_row_krx], "degraded": False})
    intent = StubIntentPort({"status": "admitted", "slot_id": "slot-1"})
    daemon = _daemon_with_holiday(slot, intent, StubMarketData(2000), now, holiday_cache=_HolidayCacheAlwaysTrading())

    result = daemon.run_decision_cycle()

    assert result["status"] == "no_op"
    assert result["intents"] == []
    diag_reasons = [d["reason"] for d in result.get("diagnostics", [])]
    assert "market_not_eligible" in diag_reasons


def test_ac15_nxt_session_without_nxt_market_suppresses_sell():
    """The common markets gate also suppresses an otherwise eligible holding sell."""
    now = datetime(2026, 6, 8, 16, 0, tzinfo=KST)
    params = {"target_weight": 0.30, "rebalance_band_pct": 5.0, "min_trade_krw": 10000}
    slot_row = holding_slot(qty=20, cash_balance=100_000, strategy_params=params)
    slot_row["markets"] = ["KRX"]
    slot = StubSlotPort({"slots": [slot_row], "degraded": False})
    intent = StubIntentPort({"status": "admitted", "slot_id": "slot-1"})
    daemon = _daemon_with_holiday(
        slot,
        intent,
        StubMarketData(5000),
        now,
        holiday_cache=_HolidayCacheAlwaysTrading(),
    )

    result = daemon.run_decision_cycle()

    assert result["status"] == "no_op"
    assert result["intents"] == []
    assert intent.sell_submitted == []
    assert "market_not_eligible" in [d["reason"] for d in result["diagnostics"]]


def test_build_daemon_does_not_wire_legacy_event_consumer():
    """Static regression: runtime construction remains snapshot/intent only."""
    import inspect

    from trex_tvr.runtime.daemon import build_daemon

    source = inspect.getsource(build_daemon)
    assert "PikaEventsConsumer" not in source
    assert "events_port=" not in source


def test_ac15_nxt_session_empty_markets_suppresses_intent():
    """REQ-005 AC-15b: NXT 전용 구간 + markets=[] → 보수 해석(["KRX"]) → intent 억제."""
    now = datetime(2026, 6, 8, 16, 0, tzinfo=KST)
    slot_row_empty = {
        "slot_id": "slot-1", "status": "inactive", "symbol": "005930",
        "qty": 0, "cash_balance": 1_000_000, "strategy_params": _TVR_PARAMS,
        "markets": [],
    }
    slot = StubSlotPort({"slots": [slot_row_empty], "degraded": False})
    intent = StubIntentPort({"status": "admitted", "slot_id": "slot-1"})
    daemon = _daemon_with_holiday(slot, intent, StubMarketData(2000), now, holiday_cache=_HolidayCacheAlwaysTrading())

    result = daemon.run_decision_cycle()

    assert result["intents"] == []
    diag_reasons = [d["reason"] for d in result.get("diagnostics", [])]
    assert "market_not_eligible" in diag_reasons


def test_ac15_nxt_session_absent_markets_suppresses_intent():
    """REQ-005 AC-15c: NXT 전용 구간 + markets 필드 없음 → 보수 해석(["KRX"]) → intent 억제."""
    now = datetime(2026, 6, 8, 16, 0, tzinfo=KST)
    slot_row_no_markets = {
        "slot_id": "slot-1", "status": "inactive", "symbol": "005930",
        "qty": 0, "cash_balance": 1_000_000, "strategy_params": _TVR_PARAMS,
        # no "markets" key
    }
    slot = StubSlotPort({"slots": [slot_row_no_markets], "degraded": False})
    intent = StubIntentPort({"status": "admitted", "slot_id": "slot-1"})
    daemon = _daemon_with_holiday(slot, intent, StubMarketData(2000), now, holiday_cache=_HolidayCacheAlwaysTrading())

    result = daemon.run_decision_cycle()

    assert result["intents"] == []
    diag_reasons = [d["reason"] for d in result.get("diagnostics", [])]
    assert "market_not_eligible" in diag_reasons


def test_ac16_quote_only_market_data_path_does_not_call_candles_endpoint():
    """REQ-003 AC-6: candle_count<=0이면 quotes만 가져오고 candles 엔드포인트는 호출하지 않는다."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)
    calls: list[tuple[str, str, dict]] = []
    transport = _build_recording_kis_transport(calls)

    slot_row = inactive_slot(
        qty=0,
        cash_balance=1_000_000,
        strategy_params={"target_weight": 0.70, "rebalance_band_pct": 5.0, "min_trade_krw": 10000},
    )
    slot = StubSlotPort({"slots": [slot_row], "degraded": False})
    intent = StubIntentPort({"status": "admitted", "slot_id": "slot-1"})

    with httpx.Client(transport=transport, base_url="https://kdh.test") as http_client:
        daemon = _daemon(
            slot,
            intent,
            KisDataHubClient(base_url="https://kdh.test", client=http_client),
            now,
        )
        result = daemon.run_decision_cycle()

    assert result["status"] == "ok"
    assert len(result["intents"]) == 1
    payload = result["intents"][0]["payload"]
    assert payload["intent_type"] == "buy"
    assert ("GET", "/v1/candles/minute", {"code": "005930", "count": "0"}) not in calls
