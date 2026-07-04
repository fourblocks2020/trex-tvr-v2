"""Shared test fixtures — stub ports + market data so the daemon runs with no infra.

BAT-001: Updated to support TVR rebalancing tests.
- StubIntentPort: added submit_sell() (AC-REQ002-3/4)
- StubSlotPort: filter recorded as-is for strategy filter assertion
- inactive_slot / holding_slot: support strategy_params, qty, cash_balance kwargs
"""
from __future__ import annotations

from datetime import timezone, timedelta

KST = timezone(timedelta(hours=9))


class StubSlotPort:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[dict] = []

    def request_slots(self, *, filter, strategy=None) -> dict:
        self.calls.append({"filter": filter, "strategy": strategy})
        return self.response


class StubIntentPort:
    """Stub IntentPort implementing both submit_buy and submit_sell (REQ-002 AC-3/4)."""

    def __init__(self, response: dict) -> None:
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


class StubEventsPort:
    def __init__(self, events: list[dict] | None = None) -> None:
        self.events = events or []

    def poll_events(self, max_messages: int = 100) -> list[dict]:
        return list(self.events)


class StubMarketData:
    """Returns a fixed quote (current_price only — TVR does not use candles)."""

    def __init__(self, current_price: float) -> None:
        self.current_price = current_price
        self.calls: list[str] = []

    def fetch_market_data(self, code: str, *, now_kst, candle_count=0) -> dict:
        self.calls.append(code)
        return {"current_price": self.current_price, "candles": [], "now_kst": now_kst}


def inactive_slot(slot_id="slot-1", symbol="005930", **extra):
    """Build an inactive slot row. strategy_params, qty, cash_balance supported via **extra."""
    return {"slot_id": slot_id, "status": "inactive", "symbol": symbol, **extra}


def holding_slot(slot_id="slot-1", symbol="005930", **extra):
    """Build a holding slot row. strategy_params, qty, cash_balance supported via **extra."""
    return {"slot_id": slot_id, "status": "holding", "symbol": symbol, **extra}
