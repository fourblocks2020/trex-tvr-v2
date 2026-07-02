"""Backtest metrics: per-entry return + entries/day."""
from __future__ import annotations

from trex_tvr.backtest.metrics import compute_metrics
from trex_tvr.backtest.replay_engine import TradeRecord


def _trade(symbol, date, entry, qty, pnl) -> TradeRecord:
    return TradeRecord(
        symbol=symbol, date=date, entry_price=entry,
        entry_time=f"{date[:4]}-{date[4:6]}-{date[6:]}T09:20:00+09:00",
        exit_price=entry, exit_time=f"{date[:4]}-{date[4:6]}-{date[6:]}T10:00:00+09:00",
        exit_reason="forced_close", qty=qty, realized_pnl=pnl,
    )


def test_empty_has_new_keys() -> None:
    m = compute_metrics([])
    assert m["avg_return_pct"] == 0.0
    assert m["avg_entries_per_day"] == 0.0
    assert m["entries_per_day_std"] == 0.0
    assert m["entries_per_day_max"] == 0


def test_avg_return_pct() -> None:
    # invested = 100*10 = 1000; return = 50/1000 = 5%. Two trades both 5% -> 5%.
    trades = [_trade("A", "20260504", 100, 10, 50), _trade("B", "20260504", 200, 5, 50)]
    # trade B: invested 200*5=1000, return 50/1000=5% -> mean 5%
    m = compute_metrics(trades)
    assert m["avg_return_pct"] == 5.0


def test_avg_entries_per_day() -> None:
    # 3 trades across 2 distinct dates -> day counts [2, 1].
    trades = [
        _trade("A", "20260504", 100, 10, 0),
        _trade("B", "20260504", 100, 10, 0),
        _trade("C", "20260506", 100, 10, 0),
    ]
    m = compute_metrics(trades)
    assert m["avg_entries_per_day"] == 1.5  # (2+1)/2, distribution center
    assert m["entries_per_day_std"] == 0.5  # pstdev([2,1])
    assert m["entries_per_day_max"] == 2
