"""Exit simulator: threshold parity, 3 exit paths, intra-bar priority, fee/slippage."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trex_tvr.backtest.exit_simulator import ExitSimulator
from trex_tvr.runtime.intent_builder import build_buy_intent
from trex_tvr.strategies.base import BuySignal

_KST = timezone(timedelta(hours=9))


def _dt(hhmm: str, date: str = "2026-06-10") -> datetime:
    return datetime.fromisoformat(f"{date}T{hhmm}:00+09:00")


def _bar(hhmm: str, low: int, high: int, close: int, date: str = "2026-06-10") -> dict:
    return {
        "datetime": f"{date}T{hhmm}:00+09:00",
        "open": close, "high": high, "low": low, "close": close, "volume": 1000,
    }


@pytest.fixture
def sim() -> ExitSimulator:
    return ExitSimulator(stop_loss_pct=2.0, take_profit_pct=4.0, fee_pct=0.0, slippage_pct=0.0)


def test_threshold_matches_intent_builder(sim: ExitSimulator) -> None:
    """thresholds must match intent_builder.build_buy_intent formula."""
    entry = 10000
    forced_close_at = _dt("11:00")

    thresholds = sim.compute_thresholds(entry, forced_close_at)
    expected_sl = round(entry * (1 - 2.0 / 100.0))   # 9800
    expected_tp = round(entry * (1 + 4.0 / 100.0))   # 10400
    assert thresholds["stop_loss_price"] == expected_sl
    assert thresholds["take_profit_price"] == expected_tp

    # Cross-check against intent_builder
    signal = BuySignal(
        symbol="005930", target_qty=10, allocated_capital=100000,
        reason="tvr_placeholder_buy", strategy_id="tvr_v1",
        entry_price=entry, forced_close_at=forced_close_at,
    )
    payload = build_buy_intent(
        signal, trading_date="2026-06-10", slot_id="slot-1",
        stop_loss_pct=2.0, take_profit_pct=4.0, strategy_id="tvr_v1", venue="KRX",
        decision_at="2026-06-10T09:15:00+09:00",
    )
    assert payload["stop_loss_price"] == thresholds["stop_loss_price"]
    assert payload["take_profit_price"] == thresholds["take_profit_price"]


def test_stop_loss_exit(sim: ExitSimulator) -> None:
    """SL trigger → reason=stop_loss."""
    entry = 10000
    candles = [
        _bar("09:16", low=9900, high=10200, close=10100),  # no trigger
        _bar("09:17", low=9799, high=10100, close=9900),   # low <= 9800 → SL
    ]
    rec = sim.simulate(entry, qty=10, entry_time=_dt("09:15"), candles=candles)
    assert rec.reason == "stop_loss"
    assert rec.exit_price == 9800  # = stop_loss_price


def test_take_profit_exit(sim: ExitSimulator) -> None:
    """TP trigger → reason=take_profit."""
    entry = 10000
    candles = [
        _bar("09:16", low=9900, high=10300, close=10300),  # no trigger (tp=10400)
        _bar("09:17", low=9900, high=10500, close=10400),  # high >= 10400 → TP
    ]
    rec = sim.simulate(entry, qty=10, entry_time=_dt("09:15"), candles=candles)
    assert rec.reason == "take_profit"
    assert rec.exit_price == 10400  # = take_profit_price


def test_forced_close_exit(sim: ExitSimulator) -> None:
    """forced_close when SL/TP not hit."""
    entry = 10000
    forced_close_at = _dt("09:17")
    candles = [
        _bar("09:16", low=9900, high=10200, close=10150),  # no trigger
        _bar("09:17", low=9900, high=10200, close=10100),  # now >= forced_close → forced_close
    ]
    rec = sim.simulate(entry, qty=10, entry_time=_dt("09:15"),
                       candles=candles, forced_close_at=forced_close_at)
    assert rec.reason == "forced_close"


def test_intra_bar_sl_takes_priority_over_tp(sim: ExitSimulator) -> None:
    """intra-bar simultaneous SL+TP → stop_loss wins (conservative)."""
    entry = 10000
    # Same bar: low=9700 (<= SL=9800) and high=10500 (>= TP=10400) → SL wins
    candles = [
        _bar("09:16", low=9700, high=10500, close=10000),
    ]
    rec = sim.simulate(entry, qty=10, entry_time=_dt("09:15"), candles=candles)
    assert rec.reason == "stop_loss"
    assert rec.exit_price == 9800


def test_fee_and_slippage_applied(sim: ExitSimulator) -> None:
    """realized_pnl reflects fee + slippage on both sides."""
    sim_with_cost = ExitSimulator(
        stop_loss_pct=2.0, take_profit_pct=4.0, fee_pct=0.015, slippage_pct=0.05
    )
    entry = 10000
    qty = 10
    candles = [_bar("09:16", low=9700, high=10500, close=10000)]
    rec = sim_with_cost.simulate(entry, qty=qty, entry_time=_dt("09:15"), candles=candles)
    # SL exit at 9800
    cost_factor = (0.015 + 0.05) / 100.0
    expected_entry_cost = entry * qty * (1 + cost_factor)
    expected_exit_revenue = 9800 * qty * (1 - cost_factor)
    expected_pnl = round(expected_exit_revenue - expected_entry_cost)
    assert rec.realized_pnl == expected_pnl


def test_fee_zero_pnl_parity(sim: ExitSimulator) -> None:
    """With zero fee/slippage, realized_pnl = (exit - entry) * qty."""
    entry = 10000
    qty = 5
    candles = [_bar("09:16", low=9900, high=10500, close=10400)]
    rec = sim.simulate(entry, qty=qty, entry_time=_dt("09:15"), candles=candles)
    assert rec.reason == "take_profit"
    expected_pnl = round((10400 - 10000) * qty)
    assert rec.realized_pnl == expected_pnl


def test_no_candles_fallback(sim: ExitSimulator) -> None:
    """No remaining candles → forced_close at entry_time with entry price."""
    rec = sim.simulate(10000, qty=1, entry_time=_dt("09:15"), candles=[])
    assert rec.reason == "forced_close"
    assert rec.exit_price == 10000


def test_sell_tax_applied_sell_side_only() -> None:
    """sell tax reduces P&L by exit*qty*tax%, entry cost unchanged."""
    entry, qty = 10000, 10
    candles = [_bar("09:16", low=9700, high=10500, close=10000)]  # SL exit at 9800
    base = ExitSimulator(stop_loss_pct=2.0, take_profit_pct=4.0, fee_pct=0.0, slippage_pct=0.0)
    taxed = ExitSimulator(
        stop_loss_pct=2.0, take_profit_pct=4.0, fee_pct=0.0, slippage_pct=0.0, sell_tax_pct=0.15
    )
    r0 = base.simulate(entry, qty=qty, entry_time=_dt("09:15"), candles=candles)
    r1 = taxed.simulate(entry, qty=qty, entry_time=_dt("09:15"), candles=candles)
    assert r0.exit_price == r1.exit_price == 9800
    # only the sell side is taxed: delta = exit_price * qty * 0.15%
    expected_delta = round(9800 * qty * 0.15 / 100.0)
    assert r0.realized_pnl - r1.realized_pnl == expected_delta


def test_sell_tax_default_zero_backward_compatible() -> None:
    """Without sell_tax_pct, behaviour is unchanged (default 0.0)."""
    sim = ExitSimulator(stop_loss_pct=2.0, take_profit_pct=4.0, fee_pct=0.0, slippage_pct=0.0)
    assert sim.sell_tax_pct == 0.0
    candles = [_bar("09:16", low=9900, high=10500, close=10400)]
    rec = sim.simulate(10000, qty=5, entry_time=_dt("09:15"), candles=candles)
    assert rec.realized_pnl == (10400 - 10000) * 5
