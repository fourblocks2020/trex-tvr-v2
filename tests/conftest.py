"""Shared test fixtures — stub ports + market data so the daemon runs with no infra."""
from __future__ import annotations

from datetime import timezone, timedelta

KST = timezone(timedelta(hours=9))


class StubSlotPort:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[dict] = []

    def request_slots(self, *, filter) -> dict:
        self.calls.append({"filter": filter})
        return self.response


class StubIntentPort:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.submitted: list[dict] = []

    def submit_buy(self, payload: dict) -> dict:
        self.submitted.append(payload)
        return {**self.response, "intent_id": payload["intent_id"]}


class StubEventsPort:
    def __init__(self, events: list[dict] | None = None) -> None:
        self.events = events or []

    def poll_events(self, max_messages: int = 100) -> list[dict]:
        return list(self.events)


class StubMarketData:
    """Returns a fixed quote (no candles needed for ExampleStrategy)."""

    def __init__(self, current_price: float) -> None:
        self.current_price = current_price
        self.calls: list[str] = []

    def fetch_market_data(self, code: str, *, now_kst, candle_count=400) -> dict:
        self.calls.append(code)
        return {"current_price": self.current_price, "candles": [], "now_kst": now_kst}


def inactive_slot(slot_id="slot-1", symbol="005930", **extra):
    return {"slot_id": slot_id, "status": "inactive", "symbol": symbol, **extra}
