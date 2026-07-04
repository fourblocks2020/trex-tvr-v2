# BAT-001 Verification

- Status: draft
- Batch: BAT-001

## Test Execution

실행 명령: `.venv/bin/pytest -v`
실행 환경: Python 3.12.3, pytest-9.1.1, Linux
실행 결과: **81 passed, 0 failed, 0 errors** (0.21s)

```
tests/backtest/...             (40 tests) all PASSED  — regression
tests/runtime/test_daemon_cycle.py   (8 tests)  all PASSED
tests/runtime/test_intent_builder.py (8 tests)  all PASSED
tests/runtime/test_observability.py  (8 tests)  all PASSED
tests/strategies/test_tvr_strategy.py (14 tests) all PASSED
tests/test_import_sweep.py           (3 tests)  all PASSED
```

## Acceptance Mapping

### REQ-001: TVR v1 리밸런싱 평가 코어 로직

| AC | 내용 요약 | 테스트 | 결과 |
|----|-----------|--------|------|
| AC-1 | BuySignal(target_qty=4) — 현재비중50%<65% | test_ac1_buy_signal_drift_below_band | PASS |
| AC-2 | 현금 부족 매수 축소 | test_ac2_buy_cash_constrained | PASS |
| AC-3 | HoldDecision(within_band) | test_ac3_hold_within_band | PASS |
| AC-4 | SellSignal(qty=4) — 초과 드리프트 | test_ac4_sell_signal_drift_above_band | PASS |
| AC-5 | HoldDecision(dust_sell) | test_ac5_dust_sell | PASS |
| AC-6 | Diagnostic(stale_quote) — price=0/None | test_ac6_stale_quote_zero, test_ac6_stale_quote_none | PASS |
| AC-7 | Diagnostic(zero_allocation) | test_ac7_zero_allocation | PASS |
| AC-8 | SellSignal.qty ≤ holding qty (과매도 방지) | test_ac8_oversell_clamp, test_ac8_sell_qty_never_exceeds_holdings | PASS |
| AC-9 | 결정성 — 동일 입력 2회 = 동일 출력 | test_ac9_deterministic | PASS |
| AC-10 | import sweep 통과 | test_ac10_import_sweep_marker + test_import_sweep.py | PASS |

### REQ-002: TVR sell-intent 경로 신설

| AC | 내용 요약 | 테스트 | 결과 |
|----|-----------|--------|------|
| AC-1 | build_sell_intent 필드 구조, stop_loss 없음 | test_ac1_build_sell_intent_fields | PASS |
| AC-2 | intent_id 멱등 | test_ac2_sell_intent_id_idempotent | PASS |
| AC-3 | IntentPort isinstance 체크 | test_ac3_intent_port_protocol_stub | PASS |
| AC-4 | stub Protocol isinstance True | test_ac3_intent_port_protocol_stub (동일) | PASS |
| AC-5 | parse_sell_intent_response 정상 | test_ac5_parse_sell_response_ok | PASS |
| AC-6 | intent_id 없으면 WireSchemaError | test_ac6_parse_sell_response_missing_intent_id | PASS |
| AC-7 | import sweep | test_import_sweep.py | PASS |
| AC-8 | qty<=0 → ValueError | test_ac8_build_sell_intent_invalid_qty, test_ac8_build_sell_intent_negative_qty | PASS |

### REQ-003: TVR decision-cycle 리밸런싱형 확장

| AC | 내용 요약 | 테스트 | 결과 |
|----|-----------|--------|------|
| AC-1 | strategy 필터 포함 요청 | test_full_cycle_emits_buy_intent_for_inactive_slot (filter 검증) | PASS |
| AC-2 | inactive+BuySignal → submit_buy, no stop_loss_price | test_full_cycle_emits_buy_intent_for_inactive_slot | PASS |
| AC-3 | holding+SellSignal → submit_sell | test_full_cycle_emits_sell_intent_for_holding_slot | PASS |
| AC-4 | strategy_params 없는 row → missing_strategy_params diagnostic | test_missing_strategy_params_skips_slot | PASS |
| AC-5 | buy already_active → diagnostics, no exception | test_already_active_buy_recorded_as_diagnostic | PASS |
| AC-6 | sell already_active → diagnostics, no exception | test_already_active_sell_recorded_as_diagnostic | PASS |
| AC-7 | degraded → degraded_hold, no intents | test_degraded_hold | PASS |
| AC-8 | buy reentry guard, sell 매 cycle 재평가 | test_buy_reentry_guard_applies_to_buy_only | PASS |
| AC-9 | entry_capital 없이 Daemon 생성 가능 | test_full_cycle_emits_buy_intent_for_inactive_slot (Config without entry_capital) | PASS |

### REQ-004: TVR status/health 관측성 day-1

| AC | 내용 요약 | 테스트 | 결과 |
|----|-----------|--------|------|
| AC-1 | 8항목 JSON 필드 존재 | test_ac1_snapshot_has_all_8_fields | PASS |
| AC-2 | cycle 완료 후 last_cycle_ts·cycle_count·last_status 갱신 | test_ac2_snapshot_updated_after_normal_cycle | PASS |
| AC-3 | skipped → idle_reason=out_of_schedule | test_ac3_skipped_cycle_idle_reason | PASS |
| AC-4 | transport 에러 → last_error+deps.rabbitmq=error | run_forever 코드 구현(통합 검증 필요) | 코드 PASS, 통합 미검증 |
| AC-5 | reconnect 후 last_error=null, deps.rabbitmq=ok | run_forever 코드 구현(통합 검증 필요) | 코드 PASS, 통합 미검증 |
| AC-6 | 쓰기 실패 → best-effort, cycle 중단 없음 | test_ac6_write_failure_best_effort | PASS |
| AC-7 | 타입 검사 | test_ac7_snapshot_field_types | PASS |

## Evidence

- Test: pytest -v → 81 passed, 0 failed (2026-07-04)
- Manual: 없음 (fixture 기반 단독 검증)
- Logs: 없음 (e2e wire 연동 미실행)

## Scope Boundary (정직 기술)

### fixture 기반 검증 (이번 batch 범위)
- REQ-001~004 모든 AC는 stub/fixture 기반 단독 단위 테스트로 검증됨.
- import sweep: strategies/ 시임 규칙 자동 검증.

### e2e wire 검증 (이번 batch 범위 외)
- REQ-003 trex-core additive wire 변경(strategy 필터 + row strategy_params 전달)은 **trex-core 미배포** 상태로 e2e 검증 불가.
  tvr 배포 후 trex-core 배포 전 구간에서 모든 슬롯이 missing_strategy_params 폴백으로 스킵됨(의도된 동작).
- REQ-004 AC-4·AC-5 (transport 에러/reconnect 시 deps 갱신)는 run_forever() 코드 내 구현됐으나 실제 transport 에러 시나리오 통합 테스트는 미수행.
- 실제 trex-core + portfolio-hub 연동 e2e 검증은 두 서비스의 additive 변경 배포 후 수행 예정.

## Result

- Ready for Release: false (팀장 confirm-batch-verification + qc-reviewer 독립 검증 대기)
- Blocking Issues:
  - trex-core additive wire 변경(strategy 필터 + strategy_params row 전달) 미배포 — tvr 단독 기능 완성, wire 통합은 순서대로
  - REQ-004 AC-4·AC-5 통합 검증 미수행 (코드 구현 완료, best-effort 스냅샷 동작 확인됨)
