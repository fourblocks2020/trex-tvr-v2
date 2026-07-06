# req-007: 다중 인스턴스 팬-인

- Status: Implemented
- Type: Integration
- Priority: High
- Owner: service-dev (tvr)

## Intent
- config `instances` 목록에 정의된 여러 trex-core 인스턴스 각각으로부터 `tvr_v1` 슬롯을 수집·병합하고, 평가 후 intent를 **출처 인스턴스 큐로 정확히 라우팅**한다.
- 부분 장애 시 해당 인스턴스 슬롯만 degraded 처리(나머지 인스턴스 계속)하고, 전체 장애 시 `degraded_hold`로 안전 대기한다.
- 멱등 `intent_id`에 인스턴스 식별자를 포함시켜 인스턴스 간 intent 중복·오라우팅을 방지한다.
- dcy-002 FR-3·FR-4·FR-5를 구현 계약으로 확정한다.

## Requirement

### config 확장
- `runtime/config.py`에 `instances: list[str]` 설정을 추가한다. 값은 trex-core `instance_key` 목록(예: `["longterm", "midterm"]`). 기존 단일 인스턴스 동작은 `instances=["default"]`(또는 단일 항목)으로 표현되어 후방호환된다.
- 각 인스턴스 항목에 대해 별도의 `SlotPort`와 `IntentPort`(또는 슬롯/인텐트 클라이언트)를 생성한다. 클라이언트 초기화는 daemon 시작 시 일괄 수행하며, 연결 실패 시 해당 인스턴스를 `degraded` 기록 후 계속 진행한다.

### 슬롯 수집 + row 태깅
- cycle 시작 시 각 인스턴스에 대해 `slots.req(strategy:"tvr_v1")` 를 순차 또는 병렬로 호출한다(구현 방식은 설계 단계 확정).
- 각 슬롯 row에 출처 인스턴스 키(`_source_instance: str`)를 태깅한다.
- 수집한 row들을 병합해 단일 평가 대상 목록을 구성한다.
- 인스턴스 A의 slot_id와 인스턴스 B의 slot_id가 동일 값일 수 있다 — 전역 유일성은 `(instance_key, slot_id)` 쌍으로만 보장한다.

### 부분/전체 장애 처리
- 개별 인스턴스 `slots.req` 호출이 실패(연결 오류·타임아웃)한 경우: 해당 인스턴스를 해당 cycle의 `degraded` 목록에 추가하고 나머지 인스턴스의 슬롯으로 계속 진행한다(`partial_degraded` 로깅).
- 모든 인스턴스 호출이 실패한 경우: cycle `status="degraded_hold"` 반환, intent 발신 없음.

### intent 라우팅
- 슬롯 row의 `_source_instance`를 기준으로 intent를 해당 인스턴스의 intent 큐로 발신한다.
- 큐 이름 규약: trex-core `instance_key` 규약을 따른다. 구체 큐 이름 포맷은 설계 단계에서 trex-core wire 계약 문서를 참조해 확정한다.
- `IntentPort`(또는 intent 클라이언트)는 인스턴스별로 독립 생성되며, 각 인스턴스의 큐에만 발신한다.

### 멱등 intent_id 확장
- 기존 buy intent_id 규약: `{strategy_id}:{trading_date}:{slot_id}` (REQ-003 계약)
- 기존 sell intent_id 규약: `sell:{strategy_id}:{trading_date}:{slot_id}` (REQ-002 계약)
- **다중 인스턴스 확장**: `slot_id`가 인스턴스 간 유일하지 않을 수 있으므로 `instance_key`를 포함한다.
  - buy: `{strategy_id}:{instance_key}:{trading_date}:{slot_id}`
  - sell: `sell:{strategy_id}:{instance_key}:{trading_date}:{slot_id}`
- `intent_builder.py`(`build_buy_intent`, `build_sell_intent`)를 갱신해 `instance_key` 파라미터를 받아 intent_id에 포함한다.
- 기존 단일 인스턴스 테스트와의 호환을 위해, `instance_key`가 `"default"`인 경우 기존 id와 동등하게 동작하는 것이 이상적이나, 포맷 변경은 일관성이 우선이며 기존 in-flight intent와의 충돌 위험 범위를 설계 단계에서 평가한다.

### 시임 규칙 유지
- 팬-인 로직은 `runtime/daemon.py`와 `runtime/config.py`에 집중한다.
- `strategies/` 는 인스턴스 개념을 알지 못하며, `evaluate()` 시그니처는 변경하지 않는다(순수 결정론적 함수 불변).

## Acceptance Criteria
1. Given config `instances=["inst_a","inst_b"]`이고 두 인스턴스 stub이 각각 2개 슬롯을 반환하는 경우, When decision-cycle 실행, Then 총 4개 슬롯이 병합·평가된다.
2. Given config `instances=["inst_a","inst_b"]`이고 inst_a 슬롯의 BuySignal, inst_b 슬롯의 SellSignal이 발생할 때, When decision-cycle 실행, Then inst_a의 intent 큐로 buy intent가, inst_b의 intent 큐로 sell intent가 발신된다(오라우팅 없음).
3. Given config `instances=["inst_a","inst_b"]`에서 inst_a의 `slots.req`가 ConnectionError 발생하는 경우, When decision-cycle 실행, Then inst_b 슬롯은 정상 처리되고, inst_a는 `partial_degraded` 로깅, inst_b intent 발신은 정상.
4. Given config `instances=["inst_a","inst_b"]`에서 두 인스턴스 모두 `slots.req`가 실패하는 경우, When decision-cycle 실행, Then `status="degraded_hold"` 반환, submit_buy/submit_sell 미호출.
5. Given config `instances=["single"]`(단일 인스턴스)인 경우, When decision-cycle 실행, Then 기존 단일 인스턴스 동작과 동등 (회귀 없음).
6. Given inst_a의 slot_id="slot_001"이고 inst_b의 slot_id="slot_001"(동일 id)인 경우, When 두 슬롯 모두 BuySignal 반환, Then intent_id가 각각 `tvr_v1:inst_a:{date}:slot_001`, `tvr_v1:inst_b:{date}:slot_001`으로 구분되어 중복이 없음.
7. Given `build_buy_intent(instance_key="inst_a", slot_id="slot_001", trading_date=...)` 호출, When intent_id 검사, Then `"inst_a"` 포함된 intent_id 반환.
8. Given `build_sell_intent(instance_key="inst_b", slot_id="slot_002", trading_date=...)` 호출, When intent_id 검사, Then `"sell:"` 접두사 + `"inst_b"` 포함된 intent_id 반환.
9. Given import sweep 테스트, When `trex_tvr/strategies/` 아래 모든 .py를 검사, Then 인스턴스 관련 식별자·팬-인 로직 import가 없음(전략 순수성 불변).
10. Given 팬-인 중 인스턴스별 watchdog 갱신(slots.req 부수효과), When 인스턴스 A의 slots.req가 성공하는 경우, Then 인스턴스 A의 watchdog 갱신됨(stub 확인). 인스턴스 B 실패 시 B watchdog은 갱신되지 않음(degraded).

## Impacted Area
- Docs:
  - `docs/srs/index.md` — Register 갱신
  - 설계 단계에서 wire 계약 문서(큐 네이밍 규약·intent_id 포맷) 작성 필요
- Modules:
  - `trex_tvr/runtime/config.py` — `instances: list[str]` 필드 추가
  - `trex_tvr/runtime/daemon.py` — 팬-인 수집·병합·라우팅 로직, 부분/전체 장애 처리
  - `trex_tvr/runtime/intent_builder.py` — `build_buy_intent`, `build_sell_intent`에 `instance_key` 파라미터 추가
  - `trex_tvr/transport/slot_client.py` — 인스턴스별 클라이언트 생성 지원(또는 연결 팩토리 패턴)
  - `trex_tvr/transport/intent_client.py` — 인스턴스별 큐 라우팅 지원
- Tests:
  - `tests/runtime/test_daemon_fanin.py` (신규) — 다중 인스턴스 stub: 병합·라우팅·부분장애·전체장애·단일인스턴스 회귀
  - `tests/runtime/test_intent_builder.py` (신규 또는 확장) — intent_id 포맷 검증(instance_key 포함)

## Notes
- **큐 네이밍 규약 (타 repo 의존)**:
  - trex-core `instance_key` 기반 intent 큐 이름 규약은 trex-core 소유다. 설계 단계에서 trex-core wire 계약 문서를 참조하거나 trex-core-dev에 확인한다. tvr 단독 검증은 stub 큐 이름으로 수행한다.
- **intent_id 포맷 변경의 in-flight 위험**:
  - 기존 배포된 intent(`{strategy_id}:{trading_date}:{slot_id}` 포맷)가 in-flight 상태일 때 포맷 변경 시 trex-core의 중복 방지 로직이 새 포맷의 intent를 "신규"로 처리할 수 있다. 설계 단계에서 in-flight 이전 방안(예: 배포 전 전체 admitted 초기화 또는 grace period)을 평가한다.
- **단일 인스턴스 후방호환**:
  - `instances=["default"]` 단일 항목 시 기존 동작 유지를 AC-5로 보장한다. intent_id 포맷이 변경되므로 기존 단독 인스턴스 배포에서도 intent_id가 바뀐다 — 위 in-flight 위험에 포함됨.
- **NFR — watchdog 정합 (dcy-002 NFR-2)**:
  - 팬-인에서 각 인스턴스의 `slots.req` 호출이 trex-core watchdog을 갱신하는 부수효과를 가진다(기존 단일 인스턴스 동작과 동일). 설계 단계에서 인스턴스별 독립 watchdog 갱신이 보장됨을 확인한다.
- **시임 규칙 유지 (dcy-002 NFR-3)**:
  - `strategies/evaluate()` 시그니처·순수성 불변. 인스턴스 개념은 `runtime/daemon.py`에만 존재. AC-9로 검증.

## Change Log

### CHG-20260707-01
- Date: 2026-07-07
- Author: 팀장(Claude) — confirm-req-implemented

#### Change Summary
- Approved → **Implemented**: bat-002 구현 완료. 독립 QC PASS-WITH-NOTES(blocker 0) — 154 passed, AC 33/33, 체결불가 구간 intent 발신 경로 부재·교차 라우팅 0 실증. PR #2 사장님 결재·머지(2c484cf, 2026-07-07) = confirm-batch-verification 완료.
- 잔여 부채(비차단): intent_builder docstring 구포맷 정정, cadence AC-2 어설션 강화. 배포 게이트: tvr 배포 직전 trex-core 재시작(intent_id 포맷 전환).

#### Delivery Trace Delta
- Batches: bat-002_20260706_session-cadence-fanin (merged, PR #2)

#### Revalidation Impact
- Reverification Needed: no / Existing Implementation Invalidated: no
- Status Recommendation: Implemented

### CHG-20260706-02
- Date: 2026-07-06
- Author: 팀장(Claude) — confirm-req

#### Change Summary
- Proposed → Approved: 팀장 검토·승인. dcy-002 세션·케이던스·팬-인 트랙. 세션 경계 7종 AC·intent_id 인스턴스 확장·폴백 계약(markets 미도달=KRX only, kdh 미배포=HOLD) 확인. 설계 인계 5건(OQ-3 보호장치·in-flight 이전·큐 규약 등) 인지.

#### Acceptance Criteria Delta
- Added/Updated/Removed: 없음

#### Revalidation Impact
- Reverification Needed: no / Existing Implementation Invalidated: no
- Status Recommendation: Approved

### CHG-20260706-01
- Date: 2026-07-06
- Author: service-dev (tvr)

#### Change Summary
- dcy-002 confirmed Discovery의 FR-3·FR-4·FR-5를 근거로 본 REQ 초안을 최초 생성했다(Status: Proposed).

#### Intent
- 단일 인스턴스 placeholder를 다중 인스턴스 팬-인으로 확장해 여러 trex-core 인스턴스의 tvr_v1 슬롯을 안전하게 수집·병합·라우팅하는 계약을 확보한다.

#### Acceptance Criteria Delta
- Added:
  - AC 1~10 신규 추가 (팬-인 병합·라우팅·부분장애·전체장애·단일인스턴스 회귀·intent_id 포맷·import sweep·watchdog)
- Updated: 없음
- Removed: 없음

#### Impacted Area Delta
- Docs:
  - docs/srs/index.md Register 신규 등록
- Modules:
  - trex_tvr/runtime/config.py 수정
  - trex_tvr/runtime/daemon.py 수정
  - trex_tvr/runtime/intent_builder.py 수정
  - trex_tvr/transport/slot_client.py 수정
  - trex_tvr/transport/intent_client.py 수정
- Tests:
  - tests/runtime/test_daemon_fanin.py 신규
  - tests/runtime/test_intent_builder.py 신규 또는 확장

#### Delivery Trace Delta
- Batches: none (draft-batch 단계에서 연결)
- Releases: none

#### Revalidation Impact
- Reverification Needed: no
- Existing Implementation Invalidated: no
- Status Recommendation: keep-current
- Follow-up Action: confirm-req에서 승인 전환 검토; 큐 네이밍 규약은 trex-core wire 계약 확인 후 설계 단계에서 확정; intent_id 포맷 변경의 in-flight 이전 방안 설계 단계에서 평가

#### Validation Plan
- tests/runtime/test_daemon_fanin.py의 다중 인스턴스 stub 케이스(병합·라우팅·부분장애·전체장애·단일 회귀) 전부 통과 + tests/runtime/test_intent_builder.py의 intent_id 포맷 케이스 통과로 AC 충족으로 본다. e2e wire 라우팅 검증은 trex-core 배포 후.
