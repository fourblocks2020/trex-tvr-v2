# BAT-002 Implementation Record

**Batch**: BAT-002 `bat-002_20260706_session-cadence-fanin`
**Discovery**: dcy-002
**REQs**: REQ-005, REQ-006, REQ-007
**Status**: Implemented
**Date**: 2026-07-06

---

## Files Changed

### New Production Files

| File | Purpose | REQs |
|------|---------|------|
| `trex_tvr/runtime/session_calendar.py` | 7-segment NXT session structure, `is_tradeable`, `is_nxt_only`, `get_session_info` | REQ-005 |

### Modified Production Files

| File | Change Summary | REQs |
|------|---------------|------|
| `trex_tvr/runtime/config.py` | Added `poll_interval`, `session_start`, `session_end`, `instances` fields; legacy `poll_interval_seconds` fallback | REQ-006, REQ-007 |
| `trex_tvr/runtime/intent_builder.py` | `make_intent_id` / `make_sell_intent_id` now accept `instance_key`; format changed to `{strategy}:{instance}:{date}:{slot}` | REQ-007 |
| `trex_tvr/runtime/daemon.py` | Session gate, NXT markets gate, drain/floor loop (`time.monotonic`), fan-in `_slot_ports`/`_intent_ports`, `build_daemon()` multi-instance construction | REQ-005, REQ-006, REQ-007 |
| `config/instance.example.toml` | `poll_interval = 60`, schedule `08:00`–`20:00`, commented `instances`/`session_start`/`session_end` examples | REQ-006, REQ-007 |
| `docs/batches/index.md` | Added BAT-002 row | — |

### New Test Files

| File | Test Count | REQs Covered |
|------|-----------|-------------|
| `tests/runtime/test_session_calendar.py` | 30 | REQ-005 (all 16 ACs) |
| `tests/runtime/test_daemon_cadence.py` | 9 | REQ-006 (all 7 ACs) |
| `tests/runtime/test_daemon_fanin.py` | 17 | REQ-007 (all 10 ACs + intent_id helpers) |

### Modified Test Files

| File | Change Summary | REQs |
|------|---------------|------|
| `tests/runtime/test_daemon_cycle.py` | Updated 1 intent_id assertion; added 6 session/NXT gate integration tests (AC-13/14/15) | REQ-005, REQ-007 |
| `tests/runtime/test_intent_builder.py` | Updated 3 intent_id assertions to new format with `"default"` instance key | REQ-007 |

---

## AC Coverage Table

### REQ-005 Session Calendar & Market Gate (16 ACs)

| AC | Description | Test(s) |
|----|-------------|---------|
| AC-1 | PRE_MARKET 08:00–08:59:59 | `test_ac1_pre_market_08_25`, `test_boundary_pre_market_start`, `test_is_tradeable_all_windows[PRE_MARKET-True]`, `test_is_nxt_only_all_windows[PRE_MARKET-True]` |
| AC-2 | PRE_AUCTION 09:00:00–09:00:29 (inclusive) | `test_ac2_pre_auction_08_51`, `test_ac3_pre_auction_09_00_29` |
| AC-3 | MAIN starts at 09:00:30 exactly | `test_ac3b_pre_auction_09_00_29_us`, `test_ac4_main_09_00_30` |
| AC-4 | MAIN 09:00:30–15:19:59 | `test_ac5_main_15_19_59` |
| AC-5 | CLOSE_AUCTION 15:20:00–15:29:59 | `test_ac6_close_auction_15_20_00`, `test_ac7_close_auction_15_29_59` |
| AC-6 | AFTER_SINGLE 15:30:00–15:39:59 | `test_ac8_after_single_15_30_00` |
| AC-7 | AFTER_MARKET 15:40:00–20:00:00 | `test_ac9_after_market_15_40_00`, `test_ac10_after_market_20_00_00` |
| AC-8 | OUT_OF_SESSION 20:00:01+ | `test_ac11_out_of_session_20_00_01` |
| AC-9 | Holiday → all windows `is_tradeable=False` | `test_ac12_holiday_tradeable_false`, `test_ac12_holiday_tradeable_false_pre_market`, `test_ac12_trading_day_tradeable_true` |
| AC-10 | `is_tradeable(PRE_MARKET/AFTER_SINGLE/AFTER_MARKET)=True` | `test_is_tradeable_all_windows` parametrized |
| AC-11 | `is_tradeable(PRE_AUCTION/CLOSE_AUCTION/OUT_OF_SESSION)=False` | `test_is_tradeable_all_windows` parametrized |
| AC-12 | `is_nxt_only(PRE_MARKET/AFTER_SINGLE/AFTER_MARKET)=True` | `test_is_nxt_only_all_windows` parametrized |
| AC-13 | Session gate in daemon cycle: non-tradeable → session_hold | `test_ac13_session_hold_on_non_tradeable_session`, `test_ac13_holiday_is_session_hold` |
| AC-14 | NXT-only session + `"NXT" in markets` → intent proceeds | `test_ac14_nxt_session_with_nxt_market_allows_intent` |
| AC-15 | NXT-only + missing/empty markets → intent suppressed (conservative fallback `["KRX"]`) | `test_ac15_nxt_session_without_nxt_market_suppresses_intent`, `test_ac15_nxt_session_empty_markets_suppresses_intent`, `test_ac15_nxt_session_absent_markets_suppresses_intent` |
| AC-16 | `session_calendar.py` import sweep (no forbidden imports) | `test_ac16_session_calendar_import_sweep` |

### REQ-006 Cadence 60s Drain/Floor (7 ACs)

| AC | Description | Test(s) |
|----|-------------|---------|
| AC-1 | `sleep(remaining)` when cycle faster than poll_interval | `test_ac1_floor_sleep_when_cycle_fast` |
| AC-2 | No sleep when cycle overruns poll_interval | `test_ac2_no_sleep_when_cycle_overruns` |
| AC-3 | `remaining == 0` → no sleep (boundary) | `test_ac3_boundary_elapsed_equals_poll_interval` |
| AC-4 | Monotonic timing across multiple cycles | `test_ac4_multiple_cycles_monotonic_mock` |
| AC-5 | No fixed `time.sleep(N)` call in `daemon.py` | `test_ac5_no_fixed_sleep_in_daemon` |
| AC-6 | `instance.example.toml` has `poll_interval = 60` | `test_ac6_instance_example_toml_has_poll_interval` |
| AC-7 | Session-hold cycle also uses floor sleep | `test_ac7_session_hold_gets_floor_sleep` |

### REQ-007 Multi-Instance Fan-In (10 ACs)

| AC | Description | Test(s) |
|----|-------------|---------|
| AC-1 | Two instances → slots merged into single collection | `test_ac1_two_instances_slots_merged` |
| AC-2 | Intent routed to source instance's port | `test_ac2_intents_routed_to_source_instance` |
| AC-3 | Partial failure → log `partial_degraded`, continue with healthy instances | `test_ac3_partial_failure_continues_with_healthy_instance` |
| AC-4 | All instances fail → `degraded_hold` | `test_ac4_total_failure_degraded_hold` |
| AC-5 | Single-instance config → backward-compatible (legacy API) | `test_ac5_single_instance_backward_compat` |
| AC-6 | Same slot_id from different instances → unique intent_ids (includes instance key) | `test_ac6_same_slot_id_different_instances_unique_intent_ids` |
| AC-7 | `build_buy_intent` includes `instance_key` in `intent_id` | `test_ac7_build_buy_intent_includes_instance_key` |
| AC-8 | `build_sell_intent` includes `instance_key` in `intent_id` | `test_ac8_build_sell_intent_includes_instance_key` |
| AC-9 | `strategies/` module has no instance-related imports | `test_ac9_strategies_no_instance_imports` |
| AC-10 | Watchdog updated per-instance (heartbeat key includes instance) | `test_ac10_watchdog_updated_per_instance` |

---

## Key Design Decisions

1. **7-segment session**: `PRE_MARKET`/`PRE_AUCTION`/`MAIN`/`CLOSE_AUCTION`/`AFTER_SINGLE`/`AFTER_MARKET`/`OUT_OF_SESSION`. Second-level boundary at `09:00:30` for MAIN start.
2. **NXT markets gate**: `row.markets` absent/empty → conservative `["KRX"]` fallback (no NXT intent). Only explicit `"NXT" in row.markets` permits intent in NXT-only sessions.
3. **Drain/floor loop**: Uses `time.monotonic()` for wall-clock-independent elapsed measurement. `remaining = poll_interval - elapsed`; `sleep(remaining)` iff `remaining > 0`. No fixed sleep anywhere.
4. **Fan-in architecture**: `_slot_ports: dict[str, SlotPort]`, `_intent_ports: dict[str, IntentPort]`. Each row tagged `_source_instance`. Reentry key is `"{instance}:{slot_id}"`.
5. **intent_id format change**: Old `{strategy}:{date}:{slot}` → New `{strategy}:{instance}:{date}:{slot}`. Default instance key `"default"` preserves existing single-instance format semantics with wire-safe extension.
6. **In-flight migration**: trex-core restart (admitted state reset) is sufficient; no grace period code needed.
7. **OQ-3 Deferred**: Extended session protections (spread guard, limit price band, session-weighted band) explicitly deferred to post-initial-operation change-req. Documented in `design.md`.
8. **Backward compat**: `Daemon(slot_port=...)` and `Daemon(slot_ports=...)` both accepted. `poll_interval_seconds` legacy key still parsed with floor=1.
9. **Queue naming**: Reuses existing `transport/base.py:queue_name()` — `{base}.{instance_key}` pattern; `instance_key="default"` → base queue name directly (no suffix).
10. **Holiday failopen**: If `HolidayCache.is_holiday()` raises → treat as non-holiday (conservative for trading continuity).
