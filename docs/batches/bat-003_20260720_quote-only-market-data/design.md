# BAT-003 Design

- Status: confirmed
- Batch: BAT-003

`fetch_market_data()` always calls `get_quote(code)`. For `candle_count <= 0`, it returns `current_price`, `candles=[]`, and `now_kst` without `get_candles()`. For positive counts it retains the existing candle request and KST same-day filtering. The daemon and all external contracts are unchanged.

Tests use `httpx.MockTransport`; no live service access is permitted.
