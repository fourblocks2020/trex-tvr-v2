# req-004: TVR status/health 관측성 day-1

- Status: Approved
- Type: Non-Functional
- Priority: Medium
- Owner: service-dev (tvr)

## Intent
- TVR 데몬이 첫 배포일(day-1)부터 외부에서 운영자가 핵심 상태를 조회할 수 있는 status/health 스냅샷을 제공해, "블랙박스 데몬" 운영의 한계를 극복한다.
- Discovery dcy-001 FR-7의 관측성 요건을 충족하고, trex-tsl-v1 운영 부재 교훈(observability 미탑재)을 반복하지 않는다.
- 노출 항목과 갱신 시점만 이 REQ에서 확정하며, 구체적인 전달 수단(파일 스냅샷·CLI)은 설계 단계에서 결정한다.

## Requirement
- TVR 데몬은 아래 항목을 포함하는 status 스냅샷을 JSON 형식으로 유지·노출한다.

  | 항목 | 타입 | 설명 | 갱신 시점 |
  |------|------|------|-----------|
  | `last_cycle_ts` | ISO-8601 문자열 (KST) | 가장 최근 decision-cycle 완료 시각 | 매 cycle 완료 후 |
  | `cycle_count` | int | 데몬 기동 이후 누적 cycle 수 | 매 cycle 완료 후 |
  | `last_status` | str | 가장 최근 cycle의 status 값 (`ok`·`skipped`·`degraded_hold`·`no_op` 등) | 매 cycle 완료 후 |
  | `last_error` | str 또는 null | 가장 최근 발생한 에러 메시지 (정상이면 null) | 에러 발생 시 갱신, 정상 cycle이면 null로 초기화 |
  | `deps.rabbitmq` | str (`ok`·`error`·`unknown`) | RabbitMQ 연결 상태 | startup 시, transport 에러 발생/회복 시 |
  | `deps.kis_data_hub` | str (`ok`·`error`·`unknown`) | kis-data-hub 헬스 상태 | startup 시, 조회 에러 발생/회복 시 |
  | `deps.redis` | str (`ok`·`error`·`unknown`) | Redis 연결 상태 | startup 시, ping 실패/회복 시 |
  | `idle_reason` | str 또는 null | cycle이 skip된 이유 (`out_of_schedule`·`holiday`·`capacity_full_hint_skip` 등), 정상 동작 중이면 null | cycle 결과 업데이트 시 |

- 데몬 기동 시 초기 스냅샷을 즉시 기록한다(deps 상태 = startup probe 결과).
- 에러 발생(transport 에러·reconnect) 시 `last_error`와 관련 `deps.*`를 갱신한다.
- 스냅샷은 운영자가 외부에서 접근 가능한 경로에 노출된다. 구체 수단(파일 경로·CLI 서브커맨드)은 설계 단계에서 결정한다. 이 REQ는 노출 항목과 갱신 시점만 확정한다.
- 스냅샷 쓰기 실패는 데몬 동작(decision-cycle)을 중단시키지 않는다(best-effort).

## Acceptance Criteria
1. Given 데몬이 기동 완료된 직후, When status 스냅샷을 조회하면, Then `last_cycle_ts`·`cycle_count`·`last_status`·`last_error`·`deps.rabbitmq`·`deps.kis_data_hub`·`deps.redis`·`idle_reason` 8개 항목이 모두 존재하는 유효한 JSON임.
2. Given 정상 cycle 1회 완료 후, When 스냅샷 조회하면, Then `last_cycle_ts`가 해당 cycle 완료 시각의 KST ISO-8601 문자열이고, `cycle_count`가 1 증가하며, `last_status`가 해당 cycle의 status 값.
3. Given cycle status="skipped", reason="out_of_schedule"인 경우, When 스냅샷 조회하면, Then `last_status`="skipped", `idle_reason`="out_of_schedule".
4. Given transport 에러 발생 시, When 스냅샷 조회하면, Then `last_error`가 에러 메시지 포함, `deps.rabbitmq`="error".
5. Given transport 에러 후 reconnect 성공, When 다음 정상 cycle 완료 후 조회하면, Then `last_error`=null, `deps.rabbitmq`="ok".
6. Given 스냅샷 쓰기 경로에 접근 불가(예: 파일 권한 없음)인 경우, When decision-cycle 실행하면, Then cycle은 정상 완료되고 데몬이 종료되지 않음(best-effort).
7. Given 스냅샷 JSON, When 파싱하면, Then 모든 필드가 명세 타입을 충족함(타입 검사 테스트).

## Impacted Area
- Docs: 없음 (관측성 설계는 설계 단계에서 결정)
- Modules:
  - `trex_tvr/runtime/daemon.py` — `run_decision_cycle()` 완료 후 스냅샷 업데이트 로직 추가, `run_forever()` 에러 처리 시 스냅샷 갱신
  - `trex_tvr/runtime/` — 스냅샷 저장/노출 모듈 신규 (구체 경로는 설계 결정)
- Tests:
  - `tests/runtime/test_observability.py` (신규) — 항목 존재·타입·갱신 시점·에러 시나리오·best-effort 쓰기 실패 케이스

## Notes
- **구체 수단 미확정 (설계 인계)**: 파일 스냅샷(`/tmp/trex_tvr_status.json` 등)·CLI `tvr status` 서브커맨드·기타 방식 중 선택은 설계 단계에서 결정한다. 이 REQ는 "무엇을, 언제" 노출하는지만 확정한다.
- **trex-tsl-v1 교훈**: tsl-v1이 관측성 없이 배포되어 운영 시 상태 파악이 어려웠다. TVR은 day-1부터 최소 스냅샷을 탑재한다.
- **deps 상태 업데이트 정밀도**: transport 에러/회복의 감지 시점이 cycle 단위이므로, `deps.*` 상태는 최근 cycle 또는 startup probe 기준이다. 실시간 헬스체크가 아님을 명시한다.
- **`last_error` 초기화**: 정상 cycle 완료 시 `last_error`를 null로 초기화할지, 마지막 에러를 유지할지는 설계 단계에서 결정. 현재 이 REQ는 "에러 발생 시 갱신, 정상 cycle이면 null로 초기화"를 요건으로 명시한다.
- NFR-4(안전) 일부(과매도 방지·현금 부족 축소)는 REQ-001 AC에 흡수. NFR-1(회전율 억제)은 밴드/min_trade_krw 파라미터 구조(REQ-001)로 설계에서 검증. 이 REQ는 관측성 독립 backlog로 승격.

## Change Log

### CHG-20260704-02
- Date: 2026-07-04
- Author: 팀장(Claude) — confirm-req

#### Change Summary
- Proposed → Approved: 팀장 검토·승인(AC 검산 포함). batch 편성 대상.

#### Acceptance Criteria Delta
- Added/Updated/Removed: 없음

#### Revalidation Impact
- Reverification Needed: no / Existing Implementation Invalidated: no
- Status Recommendation: Approved

### CHG-20260704-01
- Date: 2026-07-04
- Author: service-dev (tvr)

#### Change Summary
- dcy-001 confirmed Discovery의 FR-7을 근거로 본 REQ 초안을 최초 생성했다(Status: Proposed).

#### Intent
- TVR 데몬에 day-1 관측성을 탑재해 운영자가 핵심 상태를 외부에서 조회할 수 있는 계약을 확보한다.

#### Acceptance Criteria Delta
- Added:
  - AC 1~7 신규 추가 (8항목 존재·타입·갱신 시점·에러 시나리오·best-effort)
- Updated:
  - 없음
- Removed:
  - 없음

#### Impacted Area Delta
- Docs:
  - docs/srs/index.md Register 신규 등록
- Modules:
  - runtime/daemon.py 확장, runtime/ 스냅샷 모듈 신규
- Tests:
  - tests/runtime/test_observability.py 신규

#### Delivery Trace Delta
- Batches:
  - none (draft-batch 단계에서 연결)
- Releases:
  - none

#### Revalidation Impact
- Reverification Needed: no
- Existing Implementation Invalidated: no
- Status Recommendation: keep-current
- Follow-up Action: confirm-req에서 승인 전환 검토; 설계 단계에서 구체 수단 확정 후 필요 시 change-req

#### Validation Plan
- tests/runtime/test_observability.py의 항목 존재·타입·갱신 시점·에러·best-effort 케이스 전부 통과로 AC 충족으로 본다.
