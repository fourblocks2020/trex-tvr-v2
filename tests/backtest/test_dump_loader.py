"""DumpLoader: fixture-based tests."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from trex_tvr.backtest.dump_loader import DumpLoader


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["datetime", "open", "high", "low", "close", "volume"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _make_bar(hhmm: str, date: str = "2026-06-10", **kw) -> dict:
    return {
        "datetime": f"{date}T{hhmm}:00+09:00",
        "open": kw.get("open", 50000),
        "high": kw.get("high", 50500),
        "low": kw.get("low", 49800),
        "close": kw.get("close", 50200),
        "volume": kw.get("volume", 1000),
    }


@pytest.fixture
def dump_dir(tmp_path: Path) -> Path:
    return tmp_path / "dump"


def test_load_returns_oldest_first(dump_dir: Path) -> None:
    symbol, date = "005930", "20260610"
    bars = [
        _make_bar("09:15", close=50200),
        _make_bar("09:01", close=49900),
        _make_bar("09:30", close=50500),
    ]
    _write_csv(dump_dir / symbol / f"{date}.csv", bars)
    loader = DumpLoader(dump_dir)
    result = loader.load(symbol, date)
    times = [r["datetime"] for r in result]
    assert times == sorted(times), "result must be oldest-first"


def test_load_filters_to_date_only(dump_dir: Path) -> None:
    symbol, date = "005930", "20260610"
    # mix current-day and previous-day bars
    bars = [
        _make_bar("15:30", date="2026-06-09", close=50000),  # previous day
        _make_bar("09:01", date="2026-06-10", close=50100),  # target day
        _make_bar("09:02", date="2026-06-10", close=50200),
    ]
    _write_csv(dump_dir / symbol / f"{date}.csv", bars)
    loader = DumpLoader(dump_dir)
    result = loader.load(symbol, date)
    assert all("2026-06-10" in r["datetime"] for r in result), "must not include previous-day bars"
    assert len(result) == 2


def test_load_missing_symbol_returns_empty(dump_dir: Path) -> None:
    loader = DumpLoader(dump_dir)
    result = loader.load("999999", "20260610")
    assert result == []


def test_load_missing_date_returns_empty(dump_dir: Path) -> None:
    symbol = "005930"
    (dump_dir / symbol).mkdir(parents=True, exist_ok=True)
    loader = DumpLoader(dump_dir)
    result = loader.load(symbol, "20260611")
    assert result == []


def test_load_normalizes_int_fields(dump_dir: Path) -> None:
    symbol, date = "005930", "20260610"
    bars = [_make_bar("09:01", open=50000, high=50500, low=49800, close=50200, volume=1234)]
    _write_csv(dump_dir / symbol / f"{date}.csv", bars)
    loader = DumpLoader(dump_dir)
    result = loader.load(symbol, date)
    assert len(result) == 1
    bar = result[0]
    assert isinstance(bar["open"], int)
    assert isinstance(bar["high"], int)
    assert isinstance(bar["volume"], int)
    assert bar["close"] == 50200


def test_load_multiple_bars_correct_count(dump_dir: Path) -> None:
    symbol, date = "005930", "20260610"
    bars = [_make_bar(f"09:{i:02d}", close=50000 + i) for i in range(1, 16)]
    _write_csv(dump_dir / symbol / f"{date}.csv", bars)
    loader = DumpLoader(dump_dir)
    result = loader.load(symbol, date)
    assert len(result) == 15


def test_effective_universe_missing_reported(dump_dir: Path) -> None:
    """Symbols absent from dump are missing."""
    symbols = ["005930", "000660", "035720"]
    # Only 005930 has dump data
    date = "20260610"
    bars = [_make_bar("09:01")]
    _write_csv(dump_dir / "005930" / f"{date}.csv", bars)

    loader = DumpLoader(dump_dir)
    missing = []
    for sym in symbols:
        if not loader.load(sym, date):
            missing.append(sym)

    assert "000660" in missing
    assert "035720" in missing
    assert "005930" not in missing
