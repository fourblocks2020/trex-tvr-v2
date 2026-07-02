"""Replay engine: look-ahead guard, determinism, capacity, per-symbol reentry, strategy-agnostic."""
from __future__ import annotations

import csv
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
import pytest

from trex_tvr.backtest.dump_loader import DumpLoader, _parse_dt
from trex_tvr.backtest.exit_simulator import ExitSimulator
from trex_tvr.backtest.replay_engine import BacktestEngine, TradeRecord
from trex_tvr.strategies.base import BuyEvaluation, BuySignal, BuyStrategy, HoldDecision

_KST = timezone(timedelta(hours=9))
_DATE = "20260610"


def _dt(hhmm: str, date_str: str = "2026-06-10") -> datetime:
    return datetime.fromisoformat(f"{date_str}T{hhmm}:00+09:00")


def _bar(hhmm: str, low: int = 49800, high: int = 50500, close: int = 50200,
         date_str: str = "2026-06-10") -> dict:
    return {
        "datetime": f"{date_str}T{hhmm}:00+09:00",
        "open": close, "high": high, "low": low, "close": close, "volume": 10000,
    }


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["datetime", "open", "high", "low", "close", "volume"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class AlwaysBuyStrategy(BuyStrategy):
    """Always returns BuySignal on first eligible tick."""
    strategy_id = "always_buy"

    def evaluate(self, asset, snapshot, market_data) -> BuyEvaluation:
        now = market_data["now_kst"]
        if not isinstance(now, datetime):
            return HoldDecision(symbol=asset["symbol"], reason="no_now", strategy_id=self.strategy_id)
        price = market_data.get("current_price", 0)
        forced_close_at = datetime.combine(now.date(), time(11, 0), tzinfo=_KST)
        return BuySignal(
            symbol=asset["symbol"],
            target_qty=10,
            allocated_capital=500000,
            reason="always_buy",
            strategy_id=self.strategy_id,
            entry_price=int(price),
            forced_close_at=forced_close_at,
        )


class NeverBuyStrategy(BuyStrategy):
    """Never buys."""
    strategy_id = "never_buy"

    def evaluate(self, asset, snapshot, market_data) -> BuyEvaluation:
        return HoldDecision(symbol=asset["symbol"], reason="hold", strategy_id=self.strategy_id)


@pytest.fixture
def dump_dir(tmp_path: Path) -> Path:
    return tmp_path / "dump"


@pytest.fixture
def candle_day() -> list[dict]:
    """One trading day of minute bars: 09:00 to 10:30."""
    bars = []
    for h, m in [(9, i) for i in range(0, 31)] + [(10, i) for i in range(0, 31)]:
        bars.append(_bar(f"{h:02d}:{m:02d}", close=50000 + m * 10))
    return bars


@pytest.fixture
def sim() -> ExitSimulator:
    return ExitSimulator(stop_loss_pct=2.0, take_profit_pct=4.0, fee_pct=0.0, slippage_pct=0.0)


def test_run_day_produces_trade(dump_dir: Path, candle_day: list[dict], sim: ExitSimulator) -> None:
    """replay produces trade record + metrics."""
    strategy = AlwaysBuyStrategy()
    loader = DumpLoader(dump_dir)
    engine = BacktestEngine(strategy, loader, sim)
    trade = engine.run_day("005930", _DATE, candle_day)
    assert trade is not None
    assert isinstance(trade, TradeRecord)
    assert trade.symbol == "005930"
    assert trade.entry_price > 0


def test_look_ahead_zero_at_each_tick(dump_dir: Path, candle_day: list[dict], sim: ExitSimulator) -> None:
    """at each tick, candles_visible contains no bars after now_kst."""
    received_market_data: list[dict] = []

    class CapturingStrategy(BuyStrategy):
        strategy_id = "capture"

        def evaluate(self, asset, snapshot, market_data) -> BuyEvaluation:
            received_market_data.append(dict(market_data))
            return HoldDecision(symbol=asset["symbol"], reason="capture", strategy_id=self.strategy_id)

    engine = BacktestEngine(CapturingStrategy(), DumpLoader(dump_dir), sim)
    engine.run_day("005930", _DATE, candle_day)

    assert len(received_market_data) > 0, "strategy must have been called at least once"
    for md in received_market_data:
        now_kst = md["now_kst"]
        for bar in md["candles"]:
            bar_dt = _parse_dt(bar["datetime"])
            assert bar_dt is not None
            assert bar_dt <= now_kst, (
                f"look-ahead: bar {bar['datetime']} > now {now_kst.isoformat()}"
            )


def test_determinism_two_runs(dump_dir: Path, candle_day: list[dict], sim: ExitSimulator) -> None:
    """same dump + same params → identical trades."""
    strategy = AlwaysBuyStrategy()
    engine = BacktestEngine(strategy, DumpLoader(dump_dir), sim)
    trade1 = engine.run_day("005930", _DATE, candle_day)
    trade2 = engine.run_day("005930", _DATE, candle_day)
    assert trade1 is not None and trade2 is not None
    assert trade1.entry_price == trade2.entry_price
    assert trade1.exit_reason == trade2.exit_reason
    assert trade1.realized_pnl == trade2.realized_pnl


def test_capacity_limit(dump_dir: Path, sim: ExitSimulator) -> None:
    """capacity=2 → at most 2 entries per day across symbols."""
    symbols = ["005930", "000660", "035720"]
    date = _DATE
    bars = [_bar(f"09:{i:02d}", close=50000) for i in range(15, 31)]
    for sym in symbols:
        _write_csv(dump_dir / sym / f"{date}.csv", bars)

    strategy = AlwaysBuyStrategy()
    engine = BacktestEngine(strategy, DumpLoader(dump_dir), sim, capacity=2)
    result = engine.run_multi_day(symbols, [date])
    assert len(result.trades) <= 2


def test_per_symbol_daily_reentry_blocked(dump_dir: Path, sim: ExitSimulator) -> None:
    """same symbol cannot enter twice on the same day."""
    sym = "005930"
    date = _DATE
    bars = [_bar(f"09:{i:02d}", close=50000) for i in range(15, 31)]
    _write_csv(dump_dir / sym / f"{date}.csv", bars)

    call_count = [0]

    class CountingStrategy(BuyStrategy):
        strategy_id = "counting"

        def evaluate(self, asset, snapshot, market_data) -> BuyEvaluation:
            call_count[0] += 1
            now = market_data["now_kst"]
            forced_close_at = datetime.combine(now.date(), time(11, 0), tzinfo=_KST)
            return BuySignal(
                symbol=asset["symbol"], target_qty=1, allocated_capital=50000,
                reason="test", strategy_id=self.strategy_id,
                entry_price=int(market_data["current_price"]),
                forced_close_at=forced_close_at,
            )

    engine = BacktestEngine(CountingStrategy(), DumpLoader(dump_dir), sim, capacity=10)
    result = engine.run_multi_day([sym, sym, sym], [date])  # same symbol 3 times
    # Despite 3 entries in symbols list, per-symbol 1회 means only 1 trade
    traded_symbols = [t.symbol for t in result.trades]
    assert traded_symbols.count(sym) <= 1


def test_strategy_agnostic(dump_dir: Path, candle_day: list[dict], sim: ExitSimulator) -> None:
    """swap NeverBuyStrategy → no trades; engine code unchanged."""
    strategy = NeverBuyStrategy()
    engine = BacktestEngine(strategy, DumpLoader(dump_dir), sim)
    trade = engine.run_day("005930", _DATE, candle_day)
    assert trade is None


def test_missing_dump_reported(dump_dir: Path, sim: ExitSimulator) -> None:
    """effective universe: symbols with no dump are reported as missing."""
    symbols = ["005930", "000660"]
    date = _DATE
    # Only 005930 has data
    bars = [_bar(f"09:{i:02d}", close=50000) for i in range(15, 31)]
    _write_csv(dump_dir / "005930" / f"{date}.csv", bars)

    strategy = NeverBuyStrategy()  # won't trade; just testing reporting
    engine = BacktestEngine(strategy, DumpLoader(dump_dir), sim)
    result = engine.run_multi_day(symbols, [date])
    assert date in result.missing_symbols
    assert "000660" in result.missing_symbols[date]
    assert "005930" not in result.missing_symbols.get(date, [])


def test_no_kis_data_hub_call_during_replay(dump_dir: Path, sim: ExitSimulator) -> None:
    """backtest replay never calls kis-data-hub (local dump only).

    We verify this structurally: replay_engine and dump_loader must not import httpx.
    """
    import ast
    import pathlib

    pkg = pathlib.Path(__file__).resolve().parents[2] / "trex_tvr" / "backtest"
    forbidden = ("httpx", "trex_tvr.market_data", "trex_tvr.transport")

    for pyfile in sorted(pkg.rglob("*.py")):
        src = pyfile.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for bad in forbidden:
                        if alias.name == bad or alias.name.startswith(bad + "."):
                            # dump_job is allowed to import httpx indirectly via KisDataHubClient
                            # but should not directly import httpx in replay path files
                            if pyfile.name not in ("dump_job.py",):
                                pytest.fail(
                                    f"{pyfile.name} imports {alias.name!r} — "
                                    "replay engine must not call kis-data-hub"
                                )
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    for bad in forbidden:
                        if node.module == bad or node.module.startswith(bad + "."):
                            if pyfile.name not in ("dump_job.py",):
                                pytest.fail(
                                    f"{pyfile.name} imports from {node.module!r} — "
                                    "replay engine must not call kis-data-hub"
                                )
