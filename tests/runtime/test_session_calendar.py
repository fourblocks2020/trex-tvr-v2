"""REQ-005: 세션 캘린더 + 슬롯별 시장 적격성 게이트 단위 테스트.

AC 매핑:
  AC-1: 08:25 → PRE_MARKET, tradeable=True, nxt_only=True
  AC-2: 08:51 → PRE_AUCTION, tradeable=False
  AC-3: 09:00:29 → PRE_AUCTION, tradeable=False
  AC-4: 09:00:30 → MAIN, tradeable=True, nxt_only=False
  AC-5: 15:19:59 → MAIN, tradeable=True
  AC-6: 15:20:00 → CLOSE_AUCTION, tradeable=False
  AC-7: 15:29:59 → CLOSE_AUCTION, tradeable=False
  AC-8: 15:30:00 → AFTER_SINGLE, tradeable=True, nxt_only=True
  AC-9: 15:40:00 → AFTER_MARKET, tradeable=True, nxt_only=True
  AC-10: 19:59:59 → AFTER_MARKET, tradeable=True
  AC-11: 20:00:00 → OUT_OF_SESSION, tradeable=False
  AC-12: 휴장일 → is_tradeable=False (구간 무관)
  AC-13: is_tradeable=False → session_hold (daemon 테스트 — test_daemon_cycle.py 확장)
  AC-14: NXT 구간 + markets=["NXT","KRX"] → 정상 평가 + intent 발신 허용 (daemon 테스트)
  AC-15: NXT 구간 + markets=["KRX"] or [] → intent 억제, Diagnostic (daemon 테스트)
  AC-16: import sweep — session_calendar.py에 금지 import 없음
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from trex_tvr.runtime.session_calendar import (
    SessionWindow,
    get_session,
    get_session_info,
    is_nxt_only,
    is_tradeable,
)

KST = timezone(timedelta(hours=9))


def _kst(h, m, s=0, us=0):
    """Helper: create a KST-aware datetime for today (2026-06-08 as reference)."""
    return datetime(2026, 6, 8, h, m, s, us, tzinfo=KST)


# ---------------------------------------------------------------------------
# AC-1: 08:25 → PRE_MARKET
# ---------------------------------------------------------------------------
def test_ac1_pre_market_08_25():
    """AC-1: 08:25(KST 평일) → PRE_MARKET, tradeable=True, nxt_only=True."""
    dt = _kst(8, 25)
    w = get_session(dt)
    assert w == SessionWindow.PRE_MARKET
    assert is_tradeable(w) is True
    assert is_nxt_only(w) is True


# ---------------------------------------------------------------------------
# AC-2: 08:51 → PRE_AUCTION, tradeable=False
# ---------------------------------------------------------------------------
def test_ac2_pre_auction_08_51():
    """AC-2: 08:51(KST 평일) → PRE_AUCTION, tradeable=False."""
    dt = _kst(8, 51)
    w = get_session(dt)
    assert w == SessionWindow.PRE_AUCTION
    assert is_tradeable(w) is False


# ---------------------------------------------------------------------------
# AC-3: 09:00:29 → PRE_AUCTION (초 단위 정밀도)
# ---------------------------------------------------------------------------
def test_ac3_pre_auction_09_00_29():
    """AC-3: 09:00:29(KST 평일) → PRE_AUCTION, tradeable=False (MAIN 미도달)."""
    dt = _kst(9, 0, 29)
    w = get_session(dt)
    assert w == SessionWindow.PRE_AUCTION
    assert is_tradeable(w) is False


# ---------------------------------------------------------------------------
# AC-3b: 09:00:29.999999 → PRE_AUCTION (microsecond 경계)
# ---------------------------------------------------------------------------
def test_ac3b_pre_auction_09_00_29_us():
    """AC-3 extra: 09:00:29.999999 → PRE_AUCTION (microsecond 포함 경계)."""
    dt = _kst(9, 0, 29, 999999)
    w = get_session(dt)
    assert w == SessionWindow.PRE_AUCTION


# ---------------------------------------------------------------------------
# AC-4: 09:00:30 → MAIN
# ---------------------------------------------------------------------------
def test_ac4_main_09_00_30():
    """AC-4: 09:00:30(KST 평일) → MAIN, tradeable=True, nxt_only=False."""
    dt = _kst(9, 0, 30)
    w = get_session(dt)
    assert w == SessionWindow.MAIN
    assert is_tradeable(w) is True
    assert is_nxt_only(w) is False


# ---------------------------------------------------------------------------
# AC-5: 15:19:59 → MAIN
# ---------------------------------------------------------------------------
def test_ac5_main_15_19_59():
    """AC-5: 15:19:59(KST 평일) → MAIN, tradeable=True."""
    dt = _kst(15, 19, 59)
    w = get_session(dt)
    assert w == SessionWindow.MAIN
    assert is_tradeable(w) is True


# ---------------------------------------------------------------------------
# AC-6: 15:20:00 → CLOSE_AUCTION
# ---------------------------------------------------------------------------
def test_ac6_close_auction_15_20_00():
    """AC-6: 15:20:00(KST 평일) → CLOSE_AUCTION, tradeable=False."""
    dt = _kst(15, 20, 0)
    w = get_session(dt)
    assert w == SessionWindow.CLOSE_AUCTION
    assert is_tradeable(w) is False


# ---------------------------------------------------------------------------
# AC-7: 15:29:59 → CLOSE_AUCTION
# ---------------------------------------------------------------------------
def test_ac7_close_auction_15_29_59():
    """AC-7: 15:29:59(KST 평일) → CLOSE_AUCTION, tradeable=False."""
    dt = _kst(15, 29, 59)
    w = get_session(dt)
    assert w == SessionWindow.CLOSE_AUCTION
    assert is_tradeable(w) is False


# ---------------------------------------------------------------------------
# AC-8: 15:30:00 → AFTER_SINGLE
# ---------------------------------------------------------------------------
def test_ac8_after_single_15_30_00():
    """AC-8: 15:30:00(KST 평일) → AFTER_SINGLE, tradeable=True, nxt_only=True."""
    dt = _kst(15, 30, 0)
    w = get_session(dt)
    assert w == SessionWindow.AFTER_SINGLE
    assert is_tradeable(w) is True
    assert is_nxt_only(w) is True


# ---------------------------------------------------------------------------
# AC-9: 15:40:00 → AFTER_MARKET
# ---------------------------------------------------------------------------
def test_ac9_after_market_15_40_00():
    """AC-9: 15:40:00(KST 평일) → AFTER_MARKET, tradeable=True, nxt_only=True."""
    dt = _kst(15, 40, 0)
    w = get_session(dt)
    assert w == SessionWindow.AFTER_MARKET
    assert is_tradeable(w) is True
    assert is_nxt_only(w) is True


# ---------------------------------------------------------------------------
# AC-10: 19:59:59 → AFTER_MARKET
# ---------------------------------------------------------------------------
def test_ac10_after_market_19_59_59():
    """AC-10: 19:59:59(KST 평일) → AFTER_MARKET, tradeable=True."""
    dt = _kst(19, 59, 59)
    w = get_session(dt)
    assert w == SessionWindow.AFTER_MARKET
    assert is_tradeable(w) is True


# ---------------------------------------------------------------------------
# AC-11: 20:00:00 → OUT_OF_SESSION
# ---------------------------------------------------------------------------
def test_ac11_out_of_session_20_00_00():
    """AC-11: 20:00:00(KST 평일) → OUT_OF_SESSION, tradeable=False."""
    dt = _kst(20, 0, 0)
    w = get_session(dt)
    assert w == SessionWindow.OUT_OF_SESSION
    assert is_tradeable(w) is False


# ---------------------------------------------------------------------------
# AC-12: 휴장일 → is_tradeable=False 전 구간
# ---------------------------------------------------------------------------

class _HolidayCache:
    """Stub: always returns False (all days are holidays)."""
    def is_trading_day(self, date_str: str) -> bool:
        return False


class _TradingCache:
    """Stub: always returns True (all days are trading days)."""
    def is_trading_day(self, date_str: str) -> bool:
        return True


def test_ac12_holiday_tradeable_false():
    """AC-12: 휴장일(holiday_cache=False) → is_tradeable=False (구간 무관: MAIN 시각)."""
    dt = _kst(10, 0)  # MAIN 구간
    w, tradeable, nxt = get_session_info(dt, holiday_cache=_HolidayCache())
    assert w == SessionWindow.MAIN
    assert tradeable is False  # 휴장이므로 항상 False


def test_ac12_holiday_tradeable_false_pre_market():
    """AC-12b: 휴장일 PRE_MARKET 구간도 tradeable=False."""
    dt = _kst(8, 30)
    w, tradeable, nxt = get_session_info(dt, holiday_cache=_HolidayCache())
    assert w == SessionWindow.PRE_MARKET
    assert tradeable is False


def test_ac12_trading_day_tradeable_true():
    """AC-12 inverse: 평일 MAIN → tradeable=True."""
    dt = _kst(10, 0)
    w, tradeable, nxt = get_session_info(dt, holiday_cache=_TradingCache())
    assert w == SessionWindow.MAIN
    assert tradeable is True


# ---------------------------------------------------------------------------
# get_session_info convenience function tests
# ---------------------------------------------------------------------------

def test_get_session_info_no_holiday_cache():
    """holiday_cache=None → no holiday check; MAIN tradeable=True."""
    dt = _kst(10, 0)
    w, tradeable, nxt = get_session_info(dt, holiday_cache=None)
    assert w == SessionWindow.MAIN
    assert tradeable is True
    assert nxt is False


def test_get_session_info_nxt_session():
    """AFTER_MARKET → nxt_only=True."""
    dt = _kst(16, 0)
    w, tradeable, nxt = get_session_info(dt, holiday_cache=None)
    assert w == SessionWindow.AFTER_MARKET
    assert tradeable is True
    assert nxt is True


def test_get_session_info_holiday_cache_fail_open():
    """holiday_cache.is_trading_day raises → fail-open → treat as trading day."""
    class _ErrorCache:
        def is_trading_day(self, date_str: str) -> bool:
            raise RuntimeError("redis down")

    dt = _kst(10, 0)
    w, tradeable, nxt = get_session_info(dt, holiday_cache=_ErrorCache())
    assert tradeable is True  # fail-open: assume trading day


# ---------------------------------------------------------------------------
# AC-16: import sweep — session_calendar.py has no forbidden imports
# ---------------------------------------------------------------------------

def test_ac16_session_calendar_import_sweep():
    """AC-16: session_calendar.py does not import trex_core, transport, pika, httpx, redis."""
    import ast
    import pathlib
    src = pathlib.Path(__file__).parent.parent.parent / "trex_tvr" / "runtime" / "session_calendar.py"
    tree = ast.parse(src.read_text())
    forbidden = {"trex_core", "transport", "pika", "httpx", "redis"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            else:
                module = ""
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    assert module not in forbidden, f"session_calendar.py imports forbidden module: {module}"
                continue
            top = module.split(".")[0] if module else ""
            assert top not in forbidden, f"session_calendar.py imports forbidden module: {module}"


# ---------------------------------------------------------------------------
# Boundary: 08:00:00 (start of PRE_MARKET)
# ---------------------------------------------------------------------------

def test_boundary_pre_market_start():
    """08:00:00 is the start of PRE_MARKET."""
    dt = _kst(8, 0, 0)
    w = get_session(dt)
    assert w == SessionWindow.PRE_MARKET


def test_boundary_out_before_pre_market():
    """07:59:59 is OUT_OF_SESSION."""
    dt = _kst(7, 59, 59)
    w = get_session(dt)
    assert w == SessionWindow.OUT_OF_SESSION


def test_boundary_naive_datetime_treated_as_kst():
    """Naive datetime is treated as KST."""
    dt_naive = datetime(2026, 6, 8, 10, 0, 0)  # no tzinfo
    w = get_session(dt_naive)
    assert w == SessionWindow.MAIN


def test_boundary_utc_datetime_converted_to_kst():
    """UTC datetime (01:00 UTC = 10:00 KST) → MAIN."""
    dt_utc = datetime(2026, 6, 8, 1, 0, 0, tzinfo=timezone.utc)
    w = get_session(dt_utc)
    assert w == SessionWindow.MAIN


# ---------------------------------------------------------------------------
# is_tradeable / is_nxt_only: full coverage of all SessionWindow values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("window,expected_tradeable", [
    (SessionWindow.PRE_MARKET, True),
    (SessionWindow.PRE_AUCTION, False),
    (SessionWindow.MAIN, True),
    (SessionWindow.CLOSE_AUCTION, False),
    (SessionWindow.AFTER_SINGLE, True),
    (SessionWindow.AFTER_MARKET, True),
    (SessionWindow.OUT_OF_SESSION, False),
])
def test_is_tradeable_all_windows(window, expected_tradeable):
    assert is_tradeable(window) is expected_tradeable


@pytest.mark.parametrize("window,expected_nxt", [
    (SessionWindow.PRE_MARKET, True),
    (SessionWindow.PRE_AUCTION, False),
    (SessionWindow.MAIN, False),
    (SessionWindow.CLOSE_AUCTION, False),
    (SessionWindow.AFTER_SINGLE, True),
    (SessionWindow.AFTER_MARKET, True),
    (SessionWindow.OUT_OF_SESSION, False),
])
def test_is_nxt_only_all_windows(window, expected_nxt):
    assert is_nxt_only(window) is expected_nxt
