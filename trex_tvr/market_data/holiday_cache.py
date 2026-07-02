"""KRX holiday cache with fail-open fallback.

Decides whether a date is a KRX trading day via kis-data-hub
`/v1/market/holiday-check`, capping the upstream call to **once per trading day**
(kis-data-hub is a shared service — load constraint):

  1. process-local dict memo  ({date_str: bool})   — cycle-level dedup
  2. Redis  `trex:holiday:krx:{date_str}` (TTL = KST midnight + 3600s)  — cross-restart dedup
  3. `client.check_holiday(date_str)`  — kis-data-hub (itself Redis-cached)

Fail-open: a `null` `is_trading_day`, a Redis error, a missing `redis` package, or
a `KisDataHubClientError` all yield `True` (treat as a trading day) with a WARNING,
so a holiday-check failure never blocks the decision cycle. The bounded downside is
covered anyway (markets closed → no fresh quotes → no BuySignal anyway).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("trex_tvr")

_KST = timezone(timedelta(hours=9))


def _key(date_str: str) -> str:
    return f"trex:holiday:krx:{date_str}"


class HolidayCache:
    """Daily KRX trading-day check, dict + Redis cached, fail-open."""

    def __init__(self, redis_url: Optional[str], client: object) -> None:
        self._client = client
        self._memo: dict[str, bool] = {}
        self._redis = None
        if redis_url:
            try:
                import redis  # lazy — only when a redis_url is configured.

                self._redis = redis.Redis.from_url(redis_url)
            except Exception as exc:  # noqa: BLE001 — fail-open: run without Redis.
                logger.warning("HolidayCache: Redis unavailable (%s); proceeding without it", exc)
                self._redis = None

    def is_trading_day(self, date_str: str) -> bool:
        """True if `date_str` (YYYYMMDD) is a KRX trading day. Fail-open on any error."""
        # 1. process-local memo
        if date_str in self._memo:
            return self._memo[date_str]

        # 2. Redis
        if self._redis is not None:
            try:
                cached = self._redis.get(_key(date_str))
                if cached is not None:
                    value = cached.decode() if isinstance(cached, bytes) else str(cached)
                    result = value.strip().lower() == "true"
                    self._memo[date_str] = result
                    return result
            except Exception as exc:  # noqa: BLE001 — fail-open, fall through to API.
                logger.warning("HolidayCache: Redis get failed for %s: %s", date_str, exc)

        # 3. kis-data-hub API
        try:
            payload = self._client.check_holiday(date_str)
            is_trading = payload.get("is_trading_day")
        except Exception as exc:  # noqa: BLE001 — fail-open.
            logger.warning(
                "HolidayCache: check_holiday failed for %s: %s; fail-open -> True", date_str, exc
            )
            return True

        if is_trading is None:
            # Unknown -> fail-open. Do NOT cache (let a later cycle retry).
            logger.warning("HolidayCache: is_trading_day=None for %s; fail-open -> True", date_str)
            return True

        result = bool(is_trading)
        self._memo[date_str] = result
        if self._redis is not None:
            try:
                self._redis.set(_key(date_str), str(result).lower(), ex=self._ttl_until_kst_midnight())
            except Exception as exc:  # noqa: BLE001 — caching is best-effort.
                logger.warning("HolidayCache: Redis set failed for %s: %s", date_str, exc)
        return result

    def _ttl_until_kst_midnight(self) -> int:
        """Seconds from now until the next KST midnight + a 3600s buffer."""
        now_kst = datetime.now(tz=_KST)
        tomorrow_midnight = (now_kst + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return int((tomorrow_midnight - now_kst).total_seconds()) + 3600
