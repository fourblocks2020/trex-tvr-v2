"""Sanity tests for TvrStrategy scaffold.

Strategies are pure functions, so they test without any infra (no broker, no HTTP).
These tests validate the scaffold placeholder logic; replace alongside real TVR logic."""
from __future__ import annotations

from trex_tvr.strategies.base import BuySignal, Diagnostic, HoldDecision
from trex_tvr.strategies.example_strategy import TvrStrategy


def _asset(symbol="005930", entry_capital=1_000_000):
    return {"symbol": symbol, "slot_id": "slot-1", "entry_capital": entry_capital}


def test_buy_signal_above_floor():
    strat = TvrStrategy(min_price=1000)
    decision = strat.evaluate(_asset(), object(), {"current_price": 2000})
    assert isinstance(decision, BuySignal)
    assert decision.target_qty == 500  # 1_000_000 // 2000
    assert decision.entry_price == 2000
    assert decision.strategy_id == "tvr_v1"


def test_hold_below_floor():
    strat = TvrStrategy(min_price=5000)
    decision = strat.evaluate(_asset(), object(), {"current_price": 2000})
    assert isinstance(decision, HoldDecision) and decision.reason == "below_floor"


def test_diagnostic_on_stale_quote():
    strat = TvrStrategy()
    decision = strat.evaluate(_asset(), object(), {"current_price": None})
    assert isinstance(decision, Diagnostic) and decision.reason == "stale_quote"


def test_deterministic():
    strat = TvrStrategy(min_price=1000)
    md = {"current_price": 2000}
    assert strat.evaluate(_asset(), object(), md) == strat.evaluate(_asset(), object(), md)
