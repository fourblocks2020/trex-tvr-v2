"""세션 캘린더 — KST datetime에 대해 NXT 세션 구간 판정.

BAT-002 / REQ-005:
  세션 구간 7종:
    PRE_MARKET    08:00 <= t < 08:50  — NXT 프리마켓 (NXT 전용)
    PRE_AUCTION   08:50 <= t < 09:00:30 — KRX 시가단일가 갭 (체결 불가)
    MAIN          09:00:30 <= t < 15:20 — KRX+NXT 접속매매
    CLOSE_AUCTION 15:20 <= t < 15:30  — KRX 종가단일가 갭 (체결 불가)
    AFTER_SINGLE  15:30 <= t < 15:40  — NXT 애프터 단일가 (NXT 전용)
    AFTER_MARKET  15:40 <= t < 20:00 — NXT 접속매매 (NXT 전용)
    OUT_OF_SESSION                    — 위 구간 밖 (체결 불가)

  is_tradeable(window): PRE_AUCTION / CLOSE_AUCTION / OUT_OF_SESSION → False, 나머지 → True.
  is_nxt_only(window):  PRE_MARKET / AFTER_SINGLE / AFTER_MARKET → True, 나머지 → False.

  휴장일(holiday_cache.is_trading_day 위임): 모든 구간 is_tradeable = False.

시임 규칙: market_data, transport, redis, pika, httpx, trex_core import 금지.
holiday_cache 호출은 허용(runtime/ 모듈 간 의존).
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger("trex_tvr")

_KST = timezone(timedelta(hours=9))

# 세션 경계 — 초 단위 정밀도 유지 (time 객체 비교)
_T_PRE_MARKET_START = time(8, 0, 0)       # 08:00:00
_T_PRE_AUCTION_START = time(8, 50, 0)     # 08:50:00
_T_MAIN_START = time(9, 0, 30)            # 09:00:30 (초 단위 정밀도)
_T_CLOSE_AUCTION_START = time(15, 20, 0)  # 15:20:00
_T_AFTER_SINGLE_START = time(15, 30, 0)   # 15:30:00
_T_AFTER_MARKET_START = time(15, 40, 0)   # 15:40:00
_T_AFTER_MARKET_END = time(20, 0, 0)      # 20:00:00 (포함)


class SessionWindow(Enum):
    """KST 기준 NXT 세션 구간."""
    PRE_MARKET = "PRE_MARKET"
    PRE_AUCTION = "PRE_AUCTION"
    MAIN = "MAIN"
    CLOSE_AUCTION = "CLOSE_AUCTION"
    AFTER_SINGLE = "AFTER_SINGLE"
    AFTER_MARKET = "AFTER_MARKET"
    OUT_OF_SESSION = "OUT_OF_SESSION"


def get_session(dt: datetime) -> SessionWindow:
    """주어진 datetime(KST)에 대해 SessionWindow를 반환한다.

    dt가 naive인 경우 KST로 처리한다.
    경계는 초 단위 정밀도를 포함해 비교한다(microsecond 포함).
    20:00:00부터 OUT_OF_SESSION이다.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_KST)
    else:
        dt = dt.astimezone(_KST)

    # time 객체로 변환해 경계 비교 (microsecond 포함)
    t = dt.time().replace(tzinfo=None)

    if t < _T_PRE_MARKET_START:
        return SessionWindow.OUT_OF_SESSION
    if t < _T_PRE_AUCTION_START:
        return SessionWindow.PRE_MARKET
    if t < _T_MAIN_START:
        return SessionWindow.PRE_AUCTION
    if t < _T_CLOSE_AUCTION_START:
        return SessionWindow.MAIN
    if t < _T_AFTER_SINGLE_START:
        return SessionWindow.CLOSE_AUCTION
    if t < _T_AFTER_MARKET_START:
        return SessionWindow.AFTER_SINGLE
    if t < _T_AFTER_MARKET_END:
        return SessionWindow.AFTER_MARKET
    return SessionWindow.OUT_OF_SESSION


def is_tradeable(window: SessionWindow, *, is_holiday: bool = False) -> bool:
    """체결 가능 여부를 반환한다.

    PRE_AUCTION, CLOSE_AUCTION, OUT_OF_SESSION → False.
    나머지 → True (휴장일이면 항상 False).
    """
    if is_holiday:
        return False
    return window not in (
        SessionWindow.PRE_AUCTION,
        SessionWindow.CLOSE_AUCTION,
        SessionWindow.OUT_OF_SESSION,
    )


def is_nxt_only(window: SessionWindow) -> bool:
    """NXT 전용 구간 여부를 반환한다.

    PRE_MARKET, AFTER_SINGLE, AFTER_MARKET → True.
    나머지 → False.
    """
    return window in (
        SessionWindow.PRE_MARKET,
        SessionWindow.AFTER_SINGLE,
        SessionWindow.AFTER_MARKET,
    )


def get_session_info(
    dt: datetime,
    holiday_cache: Optional[object] = None,
) -> tuple[SessionWindow, bool, bool]:
    """편의 함수: (window, tradeable, nxt_only) 튜플을 반환한다.

    holiday_cache가 제공되면 is_trading_day(date_str)를 호출해 휴장일 판정을 위임한다.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_KST)
    else:
        dt = dt.astimezone(_KST)

    date_str = dt.strftime("%Y%m%d")
    holiday = False
    if holiday_cache is not None:
        try:
            holiday = not holiday_cache.is_trading_day(date_str)
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.warning("session_calendar: holiday_cache error for %s: %s; fail-open", date_str, exc)
            holiday = False

    window = get_session(dt)
    tradeable = is_tradeable(window, is_holiday=holiday)
    nxt = is_nxt_only(window)
    return window, tradeable, nxt
