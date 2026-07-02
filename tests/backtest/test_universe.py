"""Universe CSV parsing and from/to filtering."""
from __future__ import annotations

import textwrap
from datetime import date
from pathlib import Path

import pytest

from trex_tvr.backtest.universe import load_universe, filter_by_date


@pytest.fixture
def universe_csv(tmp_path: Path) -> Path:
    content = textwrap.dedent("""\
        code,name,from,to
        005930,삼성전자,,
        000660,SK하이닉스,2024-01-01,2026-12-31
        35720,카카오,2025-06-01,
        999999,미래종목,,2023-12-31
    """)
    p = tmp_path / "universe.csv"
    p.write_text(content, encoding="utf-8")
    return p


def test_load_universe_code_zero_pad(universe_csv: Path) -> None:
    entries = load_universe(universe_csv)
    codes = [e.code for e in entries]
    assert "005930" in codes
    assert "035720" in codes  # 35720 zero-padded to 6 digits


def test_load_universe_name(universe_csv: Path) -> None:
    entries = load_universe(universe_csv)
    by_code = {e.code: e for e in entries}
    assert by_code["005930"].name == "삼성전자"
    assert by_code["000660"].name == "SK하이닉스"


def test_load_universe_from_to_parsed(universe_csv: Path) -> None:
    entries = load_universe(universe_csv)
    by_code = {e.code: e for e in entries}
    assert by_code["005930"].from_date is None
    assert by_code["005930"].to_date is None
    assert by_code["000660"].from_date == date(2024, 1, 1)
    assert by_code["000660"].to_date == date(2026, 12, 31)
    assert by_code["035720"].from_date == date(2025, 6, 1)
    assert by_code["035720"].to_date is None


def test_filter_by_date_unlimited(universe_csv: Path) -> None:
    entries = load_universe(universe_csv)
    codes = filter_by_date(entries, date(2026, 6, 13))
    assert "005930" in codes


def test_filter_by_date_before_from_excluded(universe_csv: Path) -> None:
    entries = load_universe(universe_csv)
    codes = filter_by_date(entries, date(2023, 12, 31))
    # 000660 from=2024-01-01 → excluded
    assert "000660" not in codes
    # 035720 from=2025-06-01 → excluded
    assert "035720" not in codes


def test_filter_by_date_after_to_excluded(universe_csv: Path) -> None:
    entries = load_universe(universe_csv)
    codes = filter_by_date(entries, date(2024, 1, 1))
    # 999999 to=2023-12-31 → excluded
    assert "999999" not in codes


def test_filter_by_date_boundary_inclusive(universe_csv: Path) -> None:
    entries = load_universe(universe_csv)
    # from boundary
    codes_on_from = filter_by_date(entries, date(2024, 1, 1))
    assert "000660" in codes_on_from
    # to boundary
    codes_on_to = filter_by_date(entries, date(2026, 12, 31))
    assert "000660" in codes_on_to
    # day before from
    codes_before_from = filter_by_date(entries, date(2023, 12, 31))
    assert "000660" not in codes_before_from


def test_load_universe_empty_csv(tmp_path: Path) -> None:
    p = tmp_path / "empty.csv"
    p.write_text("code,name,from,to\n", encoding="utf-8")
    entries = load_universe(p)
    assert entries == []
