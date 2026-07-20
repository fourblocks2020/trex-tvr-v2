# BAT-003 Implementation

- Status: implemented (retry handoff r4)
- Batch: BAT-003
- Discovery: dcy-003
- REQ: REQ-003@2

## Changed Files

### Modified Files
- `trex_tvr/market_data/kis_data_hub_client.py`
  - `fetch_market_data()` now skips minute-candle fetch when `candle_count <= 0`.
  - Returns `candles=[]` for quote-only path while preserving `current_price`, `candles`, `now_kst` schema.
  - Keeps same-day candle filtering for positive counts.

### New Files
- `tests/market_data/test_kis_data_hub_client.py`
  - Added AC coverage for `candle_count = 0`, `<0`, and `>0` paths including quote-only behavior and same-day filter.
- `tests/runtime/test_daemon_cycle.py`
  - Added regression assertion that TVR quote-only buy flow does not call `/v1/candles/minute` with `count=0`.
  - Added local mock transport helper to record quote/minute endpoint calls for this boundary path.

## Test/Validation Evidence

- `.venv/bin/pytest -q tests/market_data/test_kis_data_hub_client.py tests/runtime/test_daemon_cycle.py`
  - Result: `19 passed in 0.08s`
- `.venv/bin/pytest -q`
  - Result: `158 passed in 0.29s`
- `.venv/bin/ruff check trex_tvr/market_data/kis_data_hub_client.py tests/runtime/test_daemon_cycle.py tests/market_data/test_kis_data_hub_client.py`
  - Result: `All checks passed!`
- `git diff --check`
  - No whitespace/patch format errors

## Notes

- No strategy/daemon business logic was modified beyond the market-data boundary behavior required by AC-003-6.
- Import-sweep constraints are still satisfied by existing test set (full suite passed; no new forbidden imports added).