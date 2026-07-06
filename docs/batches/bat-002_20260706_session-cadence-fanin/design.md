# BAT-002 Design

- Status: confirmed
- Batch: BAT-002

## Architecture Summary

세 REQ가 런타임 레이어에 집중한다:
1. **session_calendar (REQ-005)**: 순수 판정 모듈 신설. KST datetime → SessionWindow 열거형. 시임 규칙 적용(runtime/ 소속이므로 strategies/ 직접 제한 대상 아님, 단 trex_core import 금지).
2. **drain/floor 루프 (REQ-006)**: run_forever()의 고정 슬립을 monotonic 기반 drain/floor로 교체. poll_interval(정수, 초) 단일 설정.
3. **팬-인 (REQ-007)**: Daemon이 per-instance SlotPort/IntentPort 맵을 소유. 슬롯 수집·병합·라우팅. intent_id에 instance_key 삽입.

시임 규칙 불변: `strategies/`는 인스턴스 개념 무지. 팬-인 로직은 `runtime/daemon.py`만.

## Changed Areas

- Docs:
  - `docs/batches/bat-002_20260706_session-cadence-fanin/design.md` (이 문서) — OQ-3 Deferred 명시
  - `docs/batches/index.md` — BAT-002 register 행 추가
  - `config/instance.example.toml` — poll_interval, session_start/end, instances 항목 추가
- Modules:
  - `trex_tvr/runtime/session_calendar.py` (신규) — SessionWindow, get_session, is_tradeable, is_nxt_only
  - `trex_tvr/runtime/config.py` — session_start, session_end, poll_interval(int), instances: list[str] 필드 추가; poll_interval_seconds 제거(대체)
  - `trex_tvr/runtime/daemon.py` — 세션 게이트, NXT markets 게이트, drain/floor 루프, 팬-인 수집·병합·라우팅·부분/전체 장애
  - `trex_tvr/runtime/intent_builder.py` — make_intent_id / make_sell_intent_id에 instance_key 파라미터 추가, intent_id 포맷 변경
  - `trex_tvr/transport/slot_client.py` — 단일 인스턴스 PikaSlotClient 불변(팩토리는 daemon에서 직접 생성)
  - `trex_tvr/transport/intent_client.py` — 단일 인스턴스 PikaIntentClient 불변(팩토리는 daemon에서 직접 생성)
- Tests:
  - `tests/runtime/test_session_calendar.py` (신규) — REQ-005 AC 1~16
  - `tests/runtime/test_daemon_cadence.py` (신규) — REQ-006 AC 1~7 (monotonic mock)
  - `tests/runtime/test_daemon_fanin.py` (신규) — REQ-007 AC 1~10
  - `tests/runtime/test_intent_builder.py` (확장) — REQ-007 AC 7~8
  - `tests/runtime/test_daemon_cycle.py` (확장) — REQ-005 AC 13~15

## Key Decisions

1. **SessionWindow 열거형 경계 (REQ-005)**
   - 7구간: PRE_MARKET [08:00, 08:50), PRE_AUCTION [08:50, 09:00:30), MAIN [09:00:30, 15:20), CLOSE_AUCTION [15:20, 15:30), AFTER_SINGLE [15:30, 15:40), AFTER_MARKET [15:40, 20:00], OUT_OF_SESSION.
   - 경계 정밀도: 09:00:30은 초 단위. datetime 비교는 microsecond 포함 `>=`. AC-3(09:00:29→PRE_AUCTION), AC-4(09:00:30→MAIN).
   - 20:00:00는 AFTER_MARKET 포함(≤). 20:00:01은 OUT_OF_SESSION.
   - 휴장일: holiday_cache.is_trading_day() False → 모든 구간에서 is_tradeable=False 반환.
   - session_calendar.py는 `market_data`, `transport`, `redis`, `pika`, `httpx`, `trex_core` import 없음.

2. **poll_interval 필드 통일 (REQ-006)**
   - 기존 `poll_interval_seconds: float = 30.0` → `poll_interval: int = 60`으로 교체.
   - TOML 키는 `poll_interval` (단위: 초, 정수). 기존 `poll_interval_seconds` TOML 키는 제거(배포 시 운영 TOML 갱신 필요).
   - `instance.example.toml`에 `poll_interval = 60  # seconds` 주석 포함.
   - daemon 내부에서 `self.config.poll_interval`로만 참조. 기존 `poll_interval_seconds` 참조를 모두 교체.

3. **drain/floor 루프 구현 (REQ-006)**
   - `run_forever()`에서 cycle 시작 직전 `t_start = time.monotonic()` 기록.
   - cycle 완료 후 `elapsed = time.monotonic() - t_start`.
   - `remaining = poll_interval - elapsed`. remaining > 0이면 `time.sleep(remaining)`, 그렇지 않으면 즉시 다음 cycle.
   - session_hold도 일반 cycle로 계산(AC-7: 60s 바닥 적용).
   - 테스트: monotonic을 mock해 elapsed 주입, sleep 호출 인자 검증. 실시간 슬립 없이 단위 테스트.

4. **팬-인 아키텍처 — Daemon per-instance 포트 맵 (REQ-007)**
   - Daemon 생성자: 기존 `slot_port: SlotPort, intent_port: IntentPort` → 단일 인스턴스 경로 유지. 그 위에 팬-인을 위한 `slot_ports: dict[str, SlotPort], intent_ports: dict[str, IntentPort]`를 추가.
   - 단일 인스턴스 호환: `instances=["default"]` 또는 단일 항목이면 기존 단일 포트를 slot_ports/intent_ports 맵에 래핑해 후방호환. Daemon.__init__에서 통일.
   - 실제 구현: Daemon은 `_slot_ports: dict[str, SlotPort]`와 `_intent_ports: dict[str, IntentPort]`를 소유. 팬-인 cycle에서 각 인스턴스를 순회해 슬롯 수집.
   - build_daemon(): `config.instances` 목록에서 per-instance PikaSlotClient/PikaIntentClient 생성 → Daemon에 전달.

5. **슬롯 row 태깅 (REQ-007)**
   - `_source_instance: str` 키를 각 슬롯 row에 추가. 인스턴스 A의 slot_id와 B의 slot_id가 같아도 `(instance_key, slot_id)` 쌍으로 구분.
   - 병합된 rows 리스트를 평가 대상으로 사용.

6. **intent_id 포맷 변경 (REQ-007)**
   - buy: `{strategy_id}:{instance_key}:{trading_date}:{slot_id}`
   - sell: `sell:{strategy_id}:{instance_key}:{trading_date}:{slot_id}`
   - `make_intent_id(strategy_id, instance_key, trading_date, slot_id)` 시그니처 변경.
   - `make_sell_intent_id(strategy_id, instance_key, trading_date, slot_id)` 시그니처 변경.
   - 기존 `build_buy_intent()`, `build_sell_intent()` 호출자(daemon)도 instance_key 전달 필요.

7. **큐 네이밍 규약 (REQ-007, trex-core wire 계약 참조)**
   - 근거: trex-core `docs/interface-contract.md` Multi-instance routing 섹션.
   - 규칙: `{base}.{instance_key}`. instance_key="default"는 base 큐명 그대로(하위호환).
   - 예: `trex.slots.req.inst-a`, `trex.intent.req.inst-b`. "default"는 `trex.slots.req`, `trex.intent.req`.
   - 이미 `transport/base.py`의 `queue_name(base, instance_key)` 함수가 이 규약을 구현하고 있음. PikaSlotClient/PikaIntentClient 생성 시 instance_key를 전달하면 된다.

8. **부분/전체 장애 처리 (REQ-007)**
   - 개별 인스턴스 `slots.req` 실패: 해당 인스턴스를 `partial_degraded` 목록 추가, 나머지 계속.
   - 전체 실패: `status="degraded_hold"` 반환, intent 발신 없음.
   - watchdog: slots.req 부수효과로 인스턴스별 독립 갱신됨. 실패 인스턴스는 watchdog 미갱신.

9. **in-flight intent_id 변경 위험 평가**
   - 기존: `{strategy}:{date}:{slot}` → 신규: `{strategy}:{instance}:{date}:{slot}`. 기존 포맷의 intent가 in-flight 상태에서 배포하면 새 포맷의 동일 슬롯 intent를 trex-core가 "신규"로 처리해 재매수 가능.
   - **권장 배포 절차**: 배포 직전 trex-core 재시작(admitted 초기화). 기존 in-flight holding 슬롯은 admitted 상태를 초기화해도 already_active 방어가 없어지지 않음(slot 상태는 DB에 유지). 신규 intent_id로 동일 슬롯을 재매수 시도하면 trex-core가 already_active로 거부. 위험 범위: 재매수 시도 1회 → 거부 → 다음 cycle 재수렴. 수용 가능.
   - 결론: admitted 초기화로 충분. 별도 grace period 불필요.

10. **kdh NX/UN 현재가 미배포 폴백 (REQ-005 Notes)**
    - NXT 전용 구간(is_nxt_only=True)에서 fetch_market_data() 실패(KisDataHubClientError) 시 해당 슬롯에 Diagnostic(reason="quote_unavailable") 기록 후 스킵. 기존 KisDataHubClientError 처리 경로 재사용.

11. **OQ-3 Deferred (REQ-005 Notes — 설계 인계 필수)**
    - **OQ-3 연장세션 보호장치(스프레드 가드·지정가 폭 제한·세션별 밴드 가중)는 이 batch 범위가 아니다.**
    - 초기 운영 관찰 후 change-req로 확정한다. 이 명시를 design.md에 기록함으로써 REQ-005 AC 충족 조건을 만족한다.

12. **config.session_start/session_end 처리**
    - config에 session_start, session_end 필드를 추가한다 (기본값 "08:00", "20:00").
    - 기존 schedule 배열([start, end) 창)은 REQ-005에 의해 08:00~20:00으로 대체되지만, 기존 schedule 필드는 유지(레거시 out-of-schedule 체크 경로와 공존).
    - 실제 session gate는 session_calendar.py의 get_session()이 담당 — schedule 배열 기반 out-of-schedule 체크는 session_hold로 통합할 수 있으나, 기존 코드와의 호환을 위해 session_calendar 게이트를 schedule 체크 이후에 추가하는 방식으로 구현.
    - 단, schedule 배열이 "09:15~15:20"으로 설정된 경우 기존 out-of-schedule이 더 좁게 동작한다. REQ-005의 취지는 08:00~20:00으로 확장이므로 instance.example.toml의 schedule을 "08:00~20:00"으로 갱신한다.

## Edge Cases

- **20:00:00**: AFTER_MARKET 포함(≤). 구현: `t <= time(20, 0, 0)` 경계.
- **20:00:01**: OUT_OF_SESSION. `t > time(20, 0, 0)` → OUT_OF_SESSION.
- **09:00:30 microsecond**: datetime(2026,1,2,9,0,30,0) >= 09:00:30 → MAIN. datetime(2026,1,2,9,0,29,999999) → PRE_AUCTION.
- **휴장 + NXT 구간**: is_trading_day=False → is_tradeable=False (구간 무관).
- **markets 필드 없음 또는 []**: 보수 해석 → ["KRX"] 동등 처리 → NXT 전용 구간에서 intent 억제.
- **모든 인스턴스 slots.req 실패**: degraded_hold, intent 발신 없음.
- **단일 인스턴스 config (instances=["default"])**: 기존 단일 포트 동작과 동등(회귀 없음).
- **drain 경계(elapsed==interval)**: remaining=0 → sleep(0) 또는 즉시 다음. sleep(0)은 허용(AC-3: 경계 케이스).

## Architecture Impact

- runtime-flows: 세션 게이트가 기존 out-of-schedule 체크 뒤에 추가됨. 팬-인이 단일 slots.req → 복수 slots.req로 확장. 문서 갱신 필요하나 dcy-002 후속 안정화 후 일괄 갱신.
- project-structure: session_calendar.py 신규. 문서 갱신 대상이나 이번 batch에서는 README 반영으로 대체.

## Reference Doc Update Plan

- project-structure.md: session_calendar.py 신규 모듈 반영. 이번 batch에서 README 반영으로 대체, 전문 문서는 deferred.
- runtime-flows.md: 세션 게이트 + 팬-인 흐름 반영 대상. dcy-002 후속 일괄 갱신 예정 — 이번 batch에서 갱신 보류.
