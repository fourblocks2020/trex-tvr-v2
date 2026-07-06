# req-006: 케이던스 — 60s 바닥 + 무슬립 드레인

- Status: Implemented
- Type: Configuration
- Priority: High
- Owner: service-dev (tvr)

## Intent
- `poll_interval`을 60초 config로 고정하고, cycle 소요가 interval 이상이면 슬립 없이 즉시 다음 cycle(무슬립 드레인)을 실행해 백프레셔 없이 연속 처리한다.
- 유휴 시 60초 바닥을 보장해 busy-loop를 방지하고, 기존 placeholder 고정 슬립 루프를 대체한다.
- dcy-002 FR-2를 구현 계약으로 확정한다.

## Requirement
- `runtime/config.py`에 `poll_interval: int = 60` (단위: 초) 설정을 추가한다. 범위: 1 이상 정수. 기본값 60.
- `runtime/daemon.py`의 main loop를 다음 규칙에 따라 재구현한다:
  - cycle 시작 직전에 `t_start = monotonic()` 을 기록한다.
  - cycle(decision-cycle 포함 모든 처리) 완료 후 `elapsed = monotonic() - t_start`를 계산한다.
  - `elapsed < poll_interval`이면: `sleep(poll_interval - elapsed)` — 나머지 시간을 슬립해 총 대기를 `poll_interval`로 맞춘다(60s 바닥).
  - `elapsed >= poll_interval`이면: 슬립 없이 즉시 다음 cycle 진입(무슬립 드레인).
- 기존 `asyncio.sleep` 또는 `time.sleep(30)` 등 고정 슬립 코드를 모두 위 규칙으로 대체한다.
- `poll_interval`의 단위와 기본값은 `config/.env.example` 주석 및 `instance.example.toml`에 명시한다.

## Acceptance Criteria
1. Given `poll_interval=60`이고 cycle 소요가 10s인 경우, When main loop 한 cycle 완료, Then 잔여 약 50s 슬립 후 다음 cycle 진입(총 cycle 간격 ≈ 60s).
2. Given `poll_interval=60`이고 cycle 소요가 65s인 경우, When main loop 한 cycle 완료, Then 슬립 없이 즉시 다음 cycle 진입(무슬립 드레인).
3. Given `poll_interval=60`이고 cycle 소요가 정확히 60s인 경우, When main loop 한 cycle 완료, Then 슬립 0(또는 ε) 후 즉시 다음 cycle 진입(경계 케이스).
4. Given `poll_interval=5`(테스트용 단축)이고 cycle이 즉시 완료되는 stub인 경우, When main loop을 3회 돌리면, Then 각 cycle 시작 간격이 약 5s(±0.5s 오차 허용).
5. Given 구 코드의 고정 슬립(`sleep(30)` 또는 동등)이 존재하는 경우, When import sweep·코드 리뷰, Then 고정 슬립 코드가 없음(대체 완료).
6. Given `poll_interval` 설정이 `instance.example.toml`에 문서화된 경우, When 파일 읽기, Then `poll_interval` 키와 단위 주석 존재.
7. Given `poll_interval=60`이고 `session_hold`(REQ-005)로 인해 cycle body가 즉시 반환된 경우, When main loop 처리, Then 60s 바닥 슬립 적용(session_hold도 일반 cycle로 계산됨).

## Impacted Area
- Docs:
  - `config/instance.example.toml` — `poll_interval` 설정 항목 추가
  - `config/.env.example` — 해당 환경변수 있으면 주석 추가
  - `docs/srs/index.md` — Register 갱신
- Modules:
  - `trex_tvr/runtime/config.py` — `poll_interval` 필드 추가
  - `trex_tvr/runtime/daemon.py` — main loop 재구현(drain/floor 로직)
- Tests:
  - `tests/runtime/test_daemon_cadence.py` (신규) 또는 `tests/runtime/test_daemon.py` (확장) — cycle 타이밍 stub: monotonic mock으로 elapsed 주입, drain/floor 분기 검증

## Notes
- **watchdog 정합**: `poll_interval=60` ≪ trex-core `watchdog_timeout`(≥1800s). 팬-인(REQ-007)에서 인스턴스별 `slots.req` 부수효과로 각 인스턴스 watchdog이 갱신됨을 설계 단계에서 확인한다.
- **타이머 구현 방식**: `time.monotonic()`을 사용해 wall clock 드리프트 방지. asyncio 기반 데몬이면 `asyncio.sleep(remaining)` 사용. 구현 방식(동기/비동기)은 현재 daemon.py 구조에 맞춰 선택하며 설계 단계에서 확정.
- **AC-4 테스트 정밀도**: 실시간 슬립을 직접 검증하면 CI 속도 저하가 크다. 단위 테스트는 `monotonic` mock으로 elapsed를 주입하고 sleep 호출 인자를 검증하는 방식을 권장한다.

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
- dcy-002 confirmed Discovery의 FR-2를 근거로 본 REQ 초안을 최초 생성했다(Status: Proposed).

#### Intent
- 기존 placeholder 고정 슬립 루프를 poll_interval 기반 drain/floor 루프로 대체해 백프레셔 없는 연속 처리와 유휴 시 60s 바닥을 동시에 확보한다.

#### Acceptance Criteria Delta
- Added:
  - AC 1~7 신규 추가 (drain·floor·경계 케이스·session_hold 연동)
- Updated: 없음
- Removed: 없음

#### Impacted Area Delta
- Docs:
  - docs/srs/index.md Register 신규 등록
  - config/instance.example.toml 수정
- Modules:
  - trex_tvr/runtime/config.py 수정
  - trex_tvr/runtime/daemon.py 수정
- Tests:
  - tests/runtime/test_daemon_cadence.py 신규 또는 test_daemon.py 확장

#### Delivery Trace Delta
- Batches: none (draft-batch 단계에서 연결)
- Releases: none

#### Revalidation Impact
- Reverification Needed: no
- Existing Implementation Invalidated: no
- Status Recommendation: keep-current
- Follow-up Action: confirm-req에서 승인 전환 검토; watchdog 정합 확인은 설계 단계

#### Validation Plan
- tests/runtime/test_daemon_cadence.py 또는 test_daemon.py의 drain/floor 분기 케이스 전부 통과 + instance.example.toml에 poll_interval 문서화로 AC 충족으로 본다.
