"""kis-data-hub HTTP client.

Sources the minute candles + latest quote that the strategy needs, since
trex-core snapshots carry no price. Failures are classified via
`KisDataHubClientError(error_class ∈ {auth, network, http, schema_drift, other})`
so callers can branch (auth -> conservative stop/alert).

The client accepts an injectable `httpx.Client`, so tests drive it with
`httpx.MockTransport` without any real network.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import httpx

_CANDLE_FIELDS = ("datetime", "open", "high", "low", "close", "volume")


class KisDataHubClientError(Exception):
    """Classified kis-data-hub failure.

    error_class:
      auth         -> 401/403
      network      -> connect/timeout/transport error
      http         -> other non-2xx status
      schema_drift -> 2xx but response shape/fields unexpected
      other        -> anything else
    """

    def __init__(self, message: str, *, error_class: str) -> None:
        super().__init__(message)
        self.error_class = error_class


def _classify_status(status_code: int) -> str:
    if status_code in (401, 403):
        return "auth"
    return "http"


def _extract_list(payload: Any) -> Optional[list]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("bars", "candles", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return None


def _extract_item(payload: Any) -> Optional[dict]:
    if isinstance(payload, dict):
        for key in ("item", "quote", "data"):
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return payload
    return None


def _normalize_candle(bar: Any) -> dict:
    if not isinstance(bar, dict):
        raise KisDataHubClientError("candle is not an object", error_class="schema_drift")
    out: dict[str, Any] = {}
    for field in _CANDLE_FIELDS:
        if field not in bar:
            raise KisDataHubClientError(
                f"candle missing field {field!r}", error_class="schema_drift"
            )
        if field == "datetime":
            out[field] = bar[field]
        else:
            try:
                out[field] = float(bar[field])
            except (TypeError, ValueError) as exc:
                raise KisDataHubClientError(
                    f"candle field {field!r} not numeric", error_class="schema_drift"
                ) from exc
    return out


class KisDataHubClient:
    """Thin httpx wrapper over the kis-data-hub `/v1/*` + `/health` contract."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        *,
        timeout: float = 5.0,
        allow_stale: bool = True,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.allow_stale = allow_stale
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.Client(
            base_url=self.base_url, headers=headers, timeout=timeout
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "KisDataHubClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _post(self, path: str, json: dict) -> Any:
        try:
            resp = self._client.post(path, json=json)
        except httpx.TimeoutException as exc:
            raise KisDataHubClientError(f"timeout: {exc}", error_class="network") from exc
        except httpx.TransportError as exc:
            raise KisDataHubClientError(f"transport: {exc}", error_class="network") from exc
        if resp.status_code >= 400:
            raise KisDataHubClientError(
                f"{path} -> HTTP {resp.status_code}", error_class=_classify_status(resp.status_code)
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise KisDataHubClientError("invalid JSON body", error_class="schema_drift") from exc

    def _get(self, path: str, params: dict) -> Any:
        try:
            resp = self._client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise KisDataHubClientError(f"timeout: {exc}", error_class="network") from exc
        except httpx.TransportError as exc:
            raise KisDataHubClientError(f"transport: {exc}", error_class="network") from exc
        if resp.status_code >= 400:
            raise KisDataHubClientError(
                f"{path} -> HTTP {resp.status_code}", error_class=_classify_status(resp.status_code)
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise KisDataHubClientError("invalid JSON body", error_class="schema_drift") from exc

    def get_candles(self, code: str, count: int) -> list[dict]:
        """`GET /v1/candles/minute` — returns `count` minute bars, oldest-first.

        An empty list is a valid response (e.g. pre-market).
        """
        payload = self._get("/v1/candles/minute", {"code": code, "count": int(count)})
        raw = _extract_list(payload)
        if raw is None:
            raise KisDataHubClientError("candles list not found", error_class="schema_drift")
        return [_normalize_candle(bar) for bar in raw]

    def get_candles_historical(self, code: str, count: int, date: str, time: str) -> list[dict]:
        """`GET /v1/candles/minute?date=YYYY-MM-DD&time=HH:MM` — replay/backtest path.

        Returns up to `count` bars ending at `date time` (KST), oldest-first.
        Returns [] on 404 (non-trading day / no data). Raises on other errors.
        Used by the backtest data dump tooling (scripts/run_dump.py).
        """
        try:
            payload = self._get(
                "/v1/candles/minute",
                {"code": code, "count": int(count), "date": date, "time": time},
            )
        except KisDataHubClientError as exc:
            if "HTTP 404" in str(exc):
                return []
            raise
        raw = _extract_list(payload)
        if raw is None:
            raise KisDataHubClientError("candles list not found", error_class="schema_drift")
        return [_normalize_candle(bar) for bar in raw]

    def get_quote(self, code: str, allow_stale: Optional[bool] = None) -> dict:
        """`POST /v1/quotes/latest` — item contains `price`."""
        stale = self.allow_stale if allow_stale is None else allow_stale
        payload = self._post("/v1/quotes/latest", {"code": code, "allow_stale": stale})
        item = _extract_item(payload)
        if item is None or "price" not in item:
            raise KisDataHubClientError("quote missing price", error_class="schema_drift")
        try:
            item = {**item, "price": float(item["price"])}
        except (TypeError, ValueError) as exc:
            raise KisDataHubClientError("quote price not numeric", error_class="schema_drift") from exc
        return item

    def check_health(self) -> bool:
        """`GET /health` (unauthenticated) startup probe. True on 2xx, else raises."""
        try:
            resp = self._client.get("/health")
        except httpx.TimeoutException as exc:
            raise KisDataHubClientError(f"timeout: {exc}", error_class="network") from exc
        except httpx.TransportError as exc:
            raise KisDataHubClientError(f"transport: {exc}", error_class="network") from exc
        if resp.status_code >= 400:
            raise KisDataHubClientError(
                f"/health -> HTTP {resp.status_code}",
                error_class=_classify_status(resp.status_code),
            )
        return True

    def check_holiday(self, date: Optional[str] = None) -> dict:
        """`GET /v1/market/holiday-check` — KRX trading-day check.

        Returns the response dict, expected to carry `is_trading_day: bool | None`
        (trex-orbit / kis-data-hub contract). `date` is `YYYYMMDD`; when None the
        query param is omitted and the server uses today. HTTP/network failures
        raise `KisDataHubClientError` (the caller — HolidayCache — fails open).
        """
        params = {"date": date} if date else {}
        payload = self._get("/v1/market/holiday-check", params)
        if not isinstance(payload, dict):
            raise KisDataHubClientError("holiday-check body not an object", error_class="schema_drift")
        return payload

    def fetch_market_data(
        self,
        code: str,
        *,
        now_kst: datetime,
        candle_count: int = 400,
    ) -> dict:
        """Assemble the `market_data` dict the strategy consumes.

        Quote is always fetched fresh. Candles are the `candle_count` most recent
        minute bars, filtered to today (oldest-first). A strategy that only needs
        the quote can ignore `candles`.
        """
        quote = self.get_quote(code)
        date_key = now_kst.date().isoformat()
        if candle_count > 0:
            raw = self.get_candles(code, candle_count)
            # Keep only today's candles — the API returns the N most recent bars sorted
            # oldest-first, which may include prior-day bars. Filtering here makes
            # candles[0] the first bar of today.
            candles = [c for c in raw if str(c.get("datetime", ""))[:10] == date_key]
        else:
            # TVR-like consumers pass candle_count=0/negative to request quote-only.
            candles = []
        return {
            "current_price": quote["price"],
            "candles": candles,
            "now_kst": now_kst,
        }
