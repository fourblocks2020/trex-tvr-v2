from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from trex_tvr.market_data.kis_data_hub_client import KisDataHubClient
from tests.conftest import KST


def _mock_kis_transport(calls: list[tuple[str, str, dict]], *, candle_payload: list[dict]) -> httpx.MockTransport:
    """Mock kis transport that records every request and returns controlled fixtures."""

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path == "/v1/quotes/latest" and request.method == "POST":
            return httpx.Response(200, json={"price": "2000"})
        if request.url.path == "/v1/candles/minute" and request.method == "GET":
            return httpx.Response(200, json=candle_payload)
        return httpx.Response(404, json={"message": "unexpected path"})

    return httpx.MockTransport(_handler)


@pytest.mark.parametrize("candle_count", [0, -1])
def test_fetch_market_data_quote_only_skips_candles_endpoint(candle_count: int) -> None:
    """REQ-003 AC-6: non-positive count should skip candle fetch and return quote-only payload."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)
    calls: list[tuple[str, str, dict]] = []
    transport = _mock_kis_transport(calls, candle_payload=[])

    with httpx.Client(transport=transport, base_url="https://kdh.test") as http_client:
        market_client = KisDataHubClient(base_url="https://kdh.test", client=http_client)
        market_data = market_client.fetch_market_data("005930", now_kst=now, candle_count=candle_count)

    assert market_data == {
        "current_price": 2000.0,
        "candles": [],
        "now_kst": now,
    }
    assert ("POST", "/v1/quotes/latest", {}) in calls
    assert all(not (method == "GET" and path == "/v1/candles/minute") for method, path, _ in calls)


def test_fetch_market_data_positive_count_filters_today_only():
    """Positive candle_count should fetch candles and keep only today's rows."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)
    calls: list[tuple[str, str, dict]] = []
    candle_payload = [
        {
            "datetime": "2026-06-07T09:00:00+09:00",
            "open": "1200",
            "high": "1210",
            "low": "1190",
            "close": "1205",
            "volume": "100",
        },
        {
            "datetime": "2026-06-08T09:00:00+09:00",
            "open": "1210",
            "high": "1220",
            "low": "1200",
            "close": "1215",
            "volume": "200",
        },
        {
            "datetime": "2026-06-08T09:01:00+09:00",
            "open": "1215",
            "high": "1225",
            "low": "1210",
            "close": "1220",
            "volume": "300",
        },
    ]
    transport = _mock_kis_transport(calls, candle_payload=candle_payload)

    with httpx.Client(transport=transport, base_url="https://kdh.test") as http_client:
        market_client = KisDataHubClient(base_url="https://kdh.test", client=http_client)
        market_data = market_client.fetch_market_data("005930", now_kst=now, candle_count=3)

    assert market_data["current_price"] == 2000.0
    assert market_data["candles"] == [
        {
            "datetime": "2026-06-08T09:00:00+09:00",
            "open": 1210.0,
            "high": 1220.0,
            "low": 1200.0,
            "close": 1215.0,
            "volume": 200.0,
        },
        {
            "datetime": "2026-06-08T09:01:00+09:00",
            "open": 1215.0,
            "high": 1225.0,
            "low": 1210.0,
            "close": 1220.0,
            "volume": 300.0,
        },
    ]
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/v1/quotes/latest"
    assert calls[1] == ("GET", "/v1/candles/minute", {"code": "005930", "count": "3"})
