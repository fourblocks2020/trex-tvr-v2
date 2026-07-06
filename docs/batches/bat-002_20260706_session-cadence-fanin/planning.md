# BAT-002 Planning

- Status: confirmed
- Batch: BAT-002
- Profile: standard

## Included REQ

- REQ-005: 세션 캘린더 + 슬롯별 시장 적격성 게이트 (Integration)
- REQ-006: 케이던스 — 60s 바닥 + 무슬립 드레인 (Configuration)
- REQ-007: 다중 인스턴스 팬-인 (Integration)

## Delivery Plan

### 구현 순서

1. **REQ-005 — session_calendar.py 신설 + config 확장**
   - `trex_tvr/runtime/session_calendar.py` 신규: SessionWindow 열거형 7종, get_session(dt), is_tradeable(window), is_nxt_only(window). holiday_cache.is_trading_day() 위임.
   - `trex_tvr/runtime/config.py`: session_start="08:00", session_end="20:00" 필드 추가 (instances 필드도 동시 추가).
   - `trex_tvr/runtime/daemon.py` decision-cycle에 세션 게이트 통합: is_tradeable=False → session_hold 반환, is_nxt_only=True → markets 게이트 적용.

2. **REQ-006 — poll_interval + 드레인 루프**
   - `trex_tvr/runtime/config.py`: poll_interval: int = 60 필드 추가 (기존 poll_interval_seconds 대체 또는 병행 확인 필요).
   - `trex_tvr/runtime/daemon.py` run_forever(): 기존 고정 sleep 제거, monotonic 기반 drain/floor 루프 구현.
   - `config/instance.example.toml`: poll_interval 항목 + 단위 주석 추가.

3. **REQ-007 — intent_builder 확장 + 팬-인 로직**
   - `trex_tvr/runtime/intent_builder.py`: make_intent_id / make_sell_intent_id에 instance_key 파라미터 추가, intent_id 포맷 변경.
   - `trex_tvr/runtime/config.py`: instances: list[str] = ["default"] 필드 추가.
   - `trex_tvr/runtime/daemon.py`: 팬-인 수집·병합·라우팅 로직 추가, per-instance SlotPort/IntentPort 맵, 부분/전체 장애 처리.
   - `trex_tvr/transport/slot_client.py` / `intent_client.py`: per-instance 팩토리 패턴 지원(또는 기존 단일 인스턴스 확장).

4. **테스트 작성**
   - `tests/runtime/test_session_calendar.py` 신규: REQ-005 AC 1~16 전부
   - `tests/runtime/test_daemon.py` (또는 test_daemon_cycle.py 확장): REQ-005 AC 13~15 (session_hold, NXT gates)
   - `tests/runtime/test_daemon_cadence.py` 신규: REQ-006 AC 1~7 (monotonic mock 기반)
   - `tests/runtime/test_daemon_fanin.py` 신규: REQ-007 AC 1~10
   - `tests/runtime/test_intent_builder.py` 확장: REQ-007 AC 7~8 (intent_id instance_key 포함)

### 선행 조건

- REQ-005~007 모두 Approved (확인됨)
- bat-001 baseline: 88 tests passed

## Dependencies

- Internal:
  - `runtime/session_calendar.py` (REQ-005) → `runtime/daemon.py` 세션 게이트 통합
  - `runtime/config.py` instances/poll_interval/session_* 확장 → daemon 의존
  - `runtime/intent_builder.py` instance_key 확장 (REQ-007) → `runtime/daemon.py` 팬-인 로직
  - per-instance SlotPort/IntentPort 팩토리 → `runtime/daemon.py` 팬-인 수집
- External:
  - trex-core wire 계약: 큐명 `{base}.{instance_key}` (instance_key="default"는 base 큐명 그대로) — 이미 transport/base.py queue_name()으로 구현됨
  - portfolio-hub markets 배관: tvr 단독 검증은 fixture로 row.markets 직접 주입
  - kdh NX/UN 현재가: 미배포 시 fetch_current_price 실패 → Diagnostic(reason="quote_unavailable") 폴백

## Design Gate

- Design Required Before Implementation: yes
- Reason:
  - REQ-007의 팬-인 아키텍처가 Daemon 생성자 계약을 변경한다. 단일 SlotPort/IntentPort에서 per-instance 맵으로 교체 시 기존 단일 인스턴스 경로와 호환을 어떻게 유지할지 설계 확정 필요.
  - REQ-006의 poll_interval은 기존 poll_interval_seconds 필드와 중복 이슈가 있다 — 어느 필드를 정본으로 삼을지 설계 단계에서 결정.
  - intent_id 포맷 변경(instance_key 삽입)이 기존 in-flight intent와 충돌할 위험 — 배포 절차 평가가 design.md에 필수.
  - OQ-3 Deferred 명시 의무 (REQ-005 Notes).

## Milestones

1. REQ-005 session_calendar.py + config + daemon 게이트 완료 (import sweep 통과)
2. REQ-006 poll_interval + drain/floor 루프 완료 + instance.example.toml 문서화
3. REQ-007 intent_builder instance_key 확장 + 팬-인 daemon 로직 완료
4. 전체 pytest 통과 (기존 88 + 신규 AC 매핑 테스트)
5. draft-batch-verification 완료

## Risks

- **config 필드 중복 (poll_interval vs poll_interval_seconds)**: 기존 poll_interval_seconds를 poll_interval(정수, 초)로 교체하면 기존 TOML 호환 파손. 완화: TOML 필드명은 poll_interval로 통일하고, 기존 poll_interval_seconds를 alias로 읽거나 제거. 기존 테스트 fixture 갱신 필요.
- **intent_id 포맷 변경 in-flight 위험**: 기존 `{strategy}:{date}:{slot}` → `{strategy}:{instance}:{date}:{slot}`. trex-core가 새 포맷을 신규로 처리해 재매수 가능. 완화: 배포 직전 admitted 초기화(trex-core restart) 또는 grace period.
- **팬-인 부분 장애 처리**: 일부 인스턴스 SlotPort 실패 시 나머지 인스턴스 계속 진행. 전체 실패 시 degraded_hold. 완화: AC-3/4 stub 테스트로 분기 검증.
- **import sweep**: session_calendar.py가 trex_core를 import하지 않는지, strategies/에 인스턴스 개념이 없는지. 완화: 구현 후 import sweep 테스트로 즉시 확인.
