# req-005: 세션 캘린더 + 슬롯별 시장 적격성 게이트

- Status: Approved
- Type: Integration
- Priority: High
- Owner: service-dev (tvr)

## Intent
- 데몬이 08:00~20:00 전 구간에서 가동하되, NXT 세션 구조(프리/갭/메인/갭/애프터)를 인지해 **체결 가능 여부를 구간별로 판정**한다 — 체결 불가 구간(세션 간 갭·휴장)에는 intent를 발신하지 않고 평가만 수행한다.
- 슬롯별 `markets` 필드(portfolio-hub → trex-core 배관)를 소비해, NXT 전용 구간에서는 `"NXT" ∈ markets` 슬롯만 리밸런싱하고 나머지는 다음 KRX 세션 재수렴 대상으로 보류한다.
- dcy-002 FR-1·FR-8을 구현 계약으로 확정하고, OQ-3(연장세션 보호장치)의 설계 인계 지점을 명시한다.

## Requirement
- `runtime/config.py`에 `session_start = "08:00"`, `session_end = "20:00"` 설정을 추가한다(KST, wall clock). 데몬 main loop은 이 시간 창 밖에서는 idle로 대기한다(기존 `09:15~15:20` placeholder 대체).
- `runtime/session_calendar.py` 모듈(신규)을 구현한다. 책임: 주어진 `datetime`(KST)에 대해 **구간 판정** 함수 `get_session(dt: datetime) -> SessionWindow`를 제공한다.
  - `SessionWindow`는 열거형 또는 dataclass로 다음 구간을 표현한다:
    - `PRE_MARKET`: 08:00 ≤ t < 08:50 — NXT 프리마켓, 단일가·지정가만, 08:50 일괄체결
    - `PRE_AUCTION`: 08:50 ≤ t < 09:00:30 — KRX 시가단일가, 주문 발신 금지(양 거래소 갭)
    - `MAIN`: 09:00:30 ≤ t < 15:20 — KRX+NXT 접속매매
    - `CLOSE_AUCTION`: 15:20 ≤ t < 15:30 — KRX 종가단일가, 주문 발신 금지
    - `AFTER_SINGLE`: 15:30 ≤ t < 15:40 — NXT 애프터 단일가
    - `AFTER_MARKET`: 15:40 ≤ t ≤ 20:00 — NXT 접속매매
    - `OUT_OF_SESSION`: 위 구간 밖(20:00 초과 또는 08:00 미만)
  - `is_tradeable(window: SessionWindow) -> bool`: `PRE_AUCTION`, `CLOSE_AUCTION`, `OUT_OF_SESSION` → False, 나머지 → True.
  - `is_nxt_only(window: SessionWindow) -> bool`: `PRE_MARKET`, `AFTER_SINGLE`, `AFTER_MARKET` → True, 나머지 → False.
- `runtime/session_calendar.py`는 KRX 휴장 판정을 `market_data/holiday_cache.py`(기존 모듈)에 위임한다. 휴장일이면 전 구간 `is_tradeable` = False를 반환한다.
- `runtime/daemon.py`의 decision-cycle에서 각 cycle 시작 시 `get_session(now_kst())` 을 호출한다:
  - `is_tradeable` = False이면: 전략 평가를 실행하지 않고 `status="session_hold"` 로깅 후 즉시 반환한다.
  - `is_tradeable` = True이고 `is_nxt_only` = True이면: 슬롯별 **시장 적격성 게이트**를 적용한다(다음 항목).
  - `is_tradeable` = True이고 `is_nxt_only` = False이면: 기존 cycle 흐름 그대로(모든 슬롯 평가 + intent 발신).
- **슬롯별 시장 적격성 게이트** (NXT 전용 구간):
  - 슬롯 row에서 `markets: list[str]` 필드를 읽는다. 필드 부재 또는 `[]`인 경우 → 보수 해석: `["KRX"]`와 동등 처리.
  - `"NXT" in row.markets`이면: 해당 슬롯 정상 평가 + intent 발신 가능.
  - `"NXT" not in row.markets`이면: 해당 슬롯 평가는 수행하되 intent 발신을 억제한다. `Diagnostic(reason="market_not_eligible", session=window.name)` 로 기록하고 다음 KRX 세션(MAIN)에서 재수렴한다.
- `markets` 필드는 portfolio-hub `assets.markets` 컬럼 → trex-core 슬롯 row → tvr에 도달하는 additive wire 배관(REQ-116/055 소관)이다. 이 REQ는 tvr 소비 쪽 계약만 확정하며, wire 배관 자체는 타 repo REQ다.
- `session_calendar.py`는 `market_data`, `transport`, `redis`, `pika`, `httpx`, `trex_core`를 import하지 않는다(시임 규칙 — `holiday_cache` 호출은 허용).
- OQ-3(연장세션 보호장치: 스프레드 가드·지정가 폭 제한·세션별 밴드 가중)은 이 REQ에서 **결정하지 않는다**. 본 REQ 설계 문서에 "OQ-3 Deferred — 초기 운영 관찰 후 change-req로 확정" 명시를 의무화한다.

## Acceptance Criteria
1. Given 현재 시각이 08:25(KST, 평일)인 경우, When `get_session(dt)` 호출, Then `SessionWindow.PRE_MARKET` 반환, `is_tradeable` = True, `is_nxt_only` = True.
2. Given 현재 시각이 08:51(KST, 평일)인 경우, When `get_session(dt)` 호출, Then `SessionWindow.PRE_AUCTION` 반환, `is_tradeable` = False.
3. Given 현재 시각이 09:00:29(KST, 평일)인 경우, When `get_session(dt)` 호출, Then `SessionWindow.PRE_AUCTION` 반환, `is_tradeable` = False.
4. Given 현재 시각이 09:00:30(KST, 평일)인 경우, When `get_session(dt)` 호출, Then `SessionWindow.MAIN` 반환, `is_tradeable` = True, `is_nxt_only` = False.
5. Given 현재 시각이 15:19:59(KST, 평일)인 경우, When `get_session(dt)` 호출, Then `SessionWindow.MAIN` 반환, `is_tradeable` = True.
6. Given 현재 시각이 15:20:00(KST, 평일)인 경우, When `get_session(dt)` 호출, Then `SessionWindow.CLOSE_AUCTION` 반환, `is_tradeable` = False.
7. Given 현재 시각이 15:29:59(KST, 평일)인 경우, When `get_session(dt)` 호출, Then `SessionWindow.CLOSE_AUCTION` 반환, `is_tradeable` = False.
8. Given 현재 시각이 15:30:00(KST, 평일)인 경우, When `get_session(dt)` 호출, Then `SessionWindow.AFTER_SINGLE` 반환, `is_tradeable` = True, `is_nxt_only` = True.
9. Given 현재 시각이 15:40:00(KST, 평일)인 경우, When `get_session(dt)` 호출, Then `SessionWindow.AFTER_MARKET` 반환, `is_tradeable` = True, `is_nxt_only` = True.
10. Given 현재 시각이 20:00:00(KST, 평일)인 경우, When `get_session(dt)` 호출, Then `SessionWindow.AFTER_MARKET` 반환, `is_tradeable` = True (20:00 경계 포함).
11. Given 현재 시각이 20:00:01(KST, 평일)인 경우, When `get_session(dt)` 호출, Then `SessionWindow.OUT_OF_SESSION` 반환, `is_tradeable` = False.
12. Given holiday_cache가 현재 날짜를 휴장으로 반환하는 경우, When `get_session(dt)` 호출, Then `is_tradeable` = False (구간 무관).
13. Given `is_tradeable` = False인 세션에서 decision-cycle이 호출될 때, When cycle 실행, Then 전략 평가 미수행, `submit_buy/submit_sell` 미호출, `status="session_hold"` 로깅.
14. Given NXT 전용 구간(예: 15:45 AFTER_MARKET)에서 stub SlotPort가 `markets=["NXT","KRX"]`인 row를 반환하는 경우, When decision-cycle 실행, Then 해당 슬롯 정상 평가 + intent 발신 허용.
15. Given NXT 전용 구간에서 stub SlotPort가 `markets=["KRX"]` 또는 `markets=[]`인 row를 반환하는 경우, When decision-cycle 실행, Then 해당 슬롯 평가 수행하되 `submit_buy/submit_sell` 미호출, `Diagnostic(reason="market_not_eligible")` 기록.
16. Given import sweep 테스트, When `trex_tvr/runtime/session_calendar.py` 검사, Then `trex_core`, `transport`, `pika`, `httpx`, `redis` import 없음.

## Impacted Area
- Docs:
  - `docs/srs/index.md` — Register 갱신
  - 설계 단계에서 `docs/batches/<batch>/design.md`에 OQ-3 Deferred 명시 의무
- Modules:
  - `trex_tvr/runtime/session_calendar.py` — 신규 (SessionWindow, get_session, is_tradeable, is_nxt_only)
  - `trex_tvr/runtime/config.py` — `session_start`, `session_end` 설정 추가
  - `trex_tvr/runtime/daemon.py` — decision-cycle에 세션 게이트 + 슬롯 시장 적격성 게이트 통합
- Tests:
  - `tests/runtime/test_session_calendar.py` (신규) — 세션 경계(08:50, 09:00:30, 15:20, 15:30, 15:40, 20:00) 케이스 + 휴장 + `is_tradeable` + `is_nxt_only`
  - `tests/runtime/test_daemon.py` (확장) — session_hold 케이스, NXT 게이트 적격·비적격 케이스

## Notes
- **wire 배관 의존 (계약 가정)**:
  - `markets` 필드는 portfolio-hub `assets.markets` → trex-core 슬롯 row → tvr 경로로 전달된다. 이 배관은 타 repo REQ(REQ-116/055 계열)이며 tvr에는 미도달 상태다. tvr 단독 검증은 fixture로 `row.markets`를 직접 주입한다. wire 배관 미배포 시 `markets` 필드 부재 → 보수 해석(`["KRX"]`)으로 폴백(KRX 전용으로 동작).
  - kdh NX/UN 현재가 REQ(별도 kdh REQ)가 미배포 상태에서 NXT 전용 구간에 진입하면, `fetch_current_price(code, market="NXT")` 호출이 실패할 수 있다 → 해당 cycle HOLD 폴백(슬롯 Diagnostic `reason="quote_unavailable"`). 설계 단계에서 폴백 계약 확정.
- **OQ-3 Deferred**: 연장세션 보호장치(스프레드 가드·지정가 폭·세션별 밴드 가중)는 이 REQ 범위가 아니다. 초기 운영 관찰 후 change-req로 확정한다. 설계 문서에 이 사실을 명시해야 AC-16 검증 통과 조건을 충족한다.
- **시임 규칙**: `session_calendar.py`는 `runtime/` 소속이며, `strategies/` 가 아니므로 시임 제한 직접 적용 대상이 아니다. 그러나 `trex_core` import 금지는 repo 전역 규칙이므로 유지.
- **세션 경계 정밀도**: 09:00:30은 초 단위 정밀도다. `datetime` 비교는 초 이하를 포함한다(microsecond 포함 `>=`).

## Change Log

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
- dcy-002 confirmed Discovery의 FR-1·FR-8을 근거로 본 REQ 초안을 최초 생성했다(Status: Proposed).

#### Intent
- 데몬 세션 창을 08:00~20:00으로 확장하고 NXT 세션 구조·휴장 캘린더·슬롯별 markets 게이트 계약을 확정해 구현 가능한 요구사항 단위를 확보한다.

#### Acceptance Criteria Delta
- Added:
  - AC 1~16 신규 추가 (세션 경계 케이스 + 휴장 + session_hold + NXT 게이트 적격·비적격 + import sweep)
- Updated: 없음
- Removed: 없음

#### Impacted Area Delta
- Docs:
  - docs/srs/index.md Register 신규 등록
- Modules:
  - trex_tvr/runtime/session_calendar.py 신규
  - trex_tvr/runtime/config.py 수정
  - trex_tvr/runtime/daemon.py 수정
- Tests:
  - tests/runtime/test_session_calendar.py 신규
  - tests/runtime/test_daemon.py 확장

#### Delivery Trace Delta
- Batches: none (draft-batch 단계에서 연결)
- Releases: none

#### Revalidation Impact
- Reverification Needed: no
- Existing Implementation Invalidated: no
- Status Recommendation: keep-current
- Follow-up Action: confirm-req에서 승인 전환 검토; wire 배관 미배포 폴백 계약은 설계 단계에서 확정

#### Validation Plan
- tests/runtime/test_session_calendar.py의 모든 세션 경계 케이스 + 휴장 케이스 통과 + tests/runtime/test_daemon.py의 session_hold·NXT 게이트 케이스 통과로 AC 충족으로 본다.
