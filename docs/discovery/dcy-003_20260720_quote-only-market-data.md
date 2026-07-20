# 0. 문서 상태

- 상태: confirmed
- 문서 ID: dcy-003_20260720_quote-only-market-data
- 이슈명: TVR quote-only 시세 조달에서 `count=0` 분봉 요청 제거
- 작성 시각(KST): 2026-07-20
- 마지막 갱신 시각(KST): 2026-07-20 13:43:58
- 대체됨: 없음
- 선행 Discovery/Release: dcy-001(리밸런싱 의미론) · dcy-002(실행 케이던스) · REL-001(released)
- 관련 GitHub: trex-tvr-v1 #3 · Project #7 / Product Milestone M5.1

# 1. 계획 상태 요약

- 연계 문서 현황:
  - dcy-001 및 REQ-003은 TVR이 현재가만 사용하도록 `TvrStrategy.candle_count = 0`을 정의했다.
  - REL-001은 BAT-001·BAT-002를 포함한 M4 TVR v1을 released로 기록한다.
  - GitHub issue #3은 실제 TVR decision cycle에서 `/v1/candles/minute?count=0`이 HTTP 400으로 거부되어 평가와 buy intent 발행이 중단됨을 기록한다.
- 해석:
  - 이는 출시 후 발견된 회귀 결함이다. 기존 승인 범위(현재가 기반 TVR 평가)를 바꾸지 않고, `candle_count <= 0`의 공통 시세 클라이언트 계약을 명시해 의도된 quote-only 경로를 복구한다.

# 2. 요구사항 판정 결과

- 판정: 변경
- 근거:
  - REQ-003은 TVR의 현재가만 사용하는 평가를 명시했고, 실제 구현도 `candle_count=0`을 전달한다.
  - 그러나 `KisDataHubClient.fetch_market_data()`가 `candle_count` 값과 무관하게 `get_candles()`를 호출해 kis-data-hub의 양의 정수 `count` 계약을 위반한다.
  - 목표비중·리밸런싱·wire 계약·세션/팬-인 범위를 변경하지 않으므로 새 전략 요구사항이 아니라 REQ-003 구현의 결함 보정이다.

# 3. 문제점의 요약

- 현재 상태:
  - `_eval_slot_work()`가 `TvrStrategy.candle_count`(0)을 `fetch_market_data()`로 전달한다.
  - `fetch_market_data()`는 quote를 받은 뒤 무조건 `get_candles(code, candle_count)`를 호출한다.
  - kis-data-hub는 `count=0`을 HTTP 400으로 거부한다. 그 결과 `TvrStrategy.evaluate()` 전에 `KisDataHubClientError`가 반환되고, buy/hold/sell 판단과 intent 발행이 수행되지 않는다.
- 기대 상태:
  - `candle_count <= 0`이면 quote API만 호출하고 `candles=[]`를 포함한 market-data를 반환한다.
  - TVR decision cycle은 그 market-data로 전략을 평가해 BuySignal, SellSignal 또는 근거 있는 HoldDecision을 산출한다.
  - `candle_count > 0` 전략은 기존처럼 분봉 API를 호출하고 당일 분봉만 필터링한다.
- 이해관계자:
  - 사장님(우선순위 및 배포 결재), 팀장(거버넌스), service-dev(REQ/구현), qc-reviewer(독립 검증), 운영팀(격리·운영 관측).
- 제약사항:
  - production DB·운영 주문 경로를 사용하지 않는다. 구현 검증은 단위 테스트와 승인된 격리 환경으로 한정한다.
  - `strategies/` 순수성 및 `trex_core` 무import 규칙을 유지한다.

## 주요 사용자 시나리오

- `005930` TVR 슬롯이 target_weight 70%, 보유 5주, 현금 977,100원이고 가격이 약 255,500원인 경우, daemon은 quote만 조회하고 전략을 평가한다. 밴드 밖이면 BuySignal과 buy intent를 생성하고, 밴드 안이면 오류가 아닌 HoldDecision을 남긴다.
- 분봉을 사용하는 다른 전략이 양수 `candle_count`를 요청하면 기존 candle 조회·당일 필터링 동작은 유지된다.

# 4. 이번에 정의할 변경

- `KisDataHubClient.fetch_market_data()`의 공통 계약을 다음처럼 명시한다.
  - `candle_count <= 0`: `get_quote()`만 호출하고 `candles=[]`를 반환한다. `get_candles()`와 `/v1/candles/minute`는 호출하지 않는다.
  - `candle_count > 0`: 기존대로 quote와 candle을 조회하고, candle을 KST 당일 기준으로 필터링한다.
- quote-only 호출의 반환 형식(`current_price`, `candles`, `now_kst`)은 기존 형식을 유지한다.
- daemon의 strategy별 `candle_count` 전달 방식과 TVR의 `candle_count=0` 선언은 변경하지 않는다.

## 영향 범위

- 코드 영향:
  - `trex_tvr/market_data/kis_data_hub_client.py` — quote-only 분기.
- 테스트 영향:
  - market-data client 단위 테스트 — zero/음수 count에서 candle endpoint 미호출, 빈 candles 반환.
  - market-data client 회귀 테스트 — 양수 count에서 candle endpoint 호출 및 기존 필터링 유지.
  - daemon cycle 통합 단위 테스트 — TVR `candle_count=0` 입력에서 BuySignal 경로가 buy intent 제출까지 진행.
- 문서 영향:
  - REQ-003 change request로 AC 및 delivery trace를 갱신한다.
- 운영 영향:
  - 배포·라이브 주문·외부 API 계약 변경 없음. 수정 전의 `count=0` HTTP 400 로그가 재발하지 않아야 한다.

# 5. 요구사항 목록

## 기능 요구사항

- FR-1: `fetch_market_data()`는 `candle_count <= 0`일 때 최신 quote만 조회하고 `candles=[]`를 반환한다.
- FR-2: `candle_count <= 0` 경로에서 `/v1/candles/minute` 요청은 절대 발생하지 않는다.
- FR-3: `candle_count > 0` 경로는 기존 candle endpoint 호출과 당일 candle 필터링 동작을 보존한다.
- FR-4: TVR daemon decision cycle은 quote-only market-data를 이용해 전략 평가를 수행하며, BuySignal이면 정상적으로 buy intent를 제출한다.

## 비기능 요구사항

- NFR-1: 반환 market-data schema는 모든 count 값에서 `current_price`, `candles`, `now_kst` 키를 유지한다.
- NFR-2: quote-only 경로는 분봉 HTTP 요청을 제거해 불필요한 외부 호출과 HTTP 400을 만들지 않는다.
- NFR-3: 전략 순수성 및 기존 import sweep 규칙은 변하지 않는다.

## 범위 경계

- In Scope: quote-only market-data 분기, 단위/회귀 테스트, REQ-003 변경 이력.
- Out of Scope: kis-data-hub API 변경, TVR 목표비중 산식 변경, strategy/wire 계약 변경, polling/세션/팬-인 변경, 운영 배포 및 실주문 검증.

# 6. 리스크/가정 목록

## 리스크

- R-1 (Medium): 조건 분기 구현이 양수 candle 전략의 기존 경로를 손상시킬 수 있다.
  - 완화 방안: `candle_count > 0` endpoint 호출과 당일 filtering 회귀 테스트를 추가한다.
- R-2 (Low): quote-only 반환 형식이 달라지면 전략 또는 daemon이 키 누락으로 실패할 수 있다.
  - 완화 방안: count와 무관하게 기존 세 키를 반환하고 daemon cycle 테스트로 확인한다.

## 가정

- A-1: kis-data-hub quote endpoint는 TVR 평가에 필요한 `price`를 반환한다. 이는 Issue #3의 기존 검증에서 HTTP 200으로 확인됐다.
- A-2: `candle_count`가 0 이하인 전략은 candle을 소비하지 않으며, 빈 리스트가 올바른 입력이다.

## 오픈 질문

- 없음. Issue #3의 기대 동작과 권장 수정 범위가 `candle_count <= 0` 공통 계약으로 명시되어 있다.

# 7. 초기 성공 기준

- S-1: `candle_count=0` 및 음수 count에서 quote 요청은 1회이고 candle 요청은 0회이며 반환 `candles == []`이다.
- S-2: 양수 count에서 candle 요청이 발생하고 기존 당일 filtering 결과가 보존된다.
- S-3: TVR fixture cycle에서 `KisDataHubClientError` 없이 BuySignal을 처리해 buy intent 1건을 제출한다.
- S-4: 전체 관련 pytest 및 import sweep이 통과한다.

## 측정 방식 및 데이터 출처

- S-1, S-2: HTTP transport stub의 호출 기록 및 market-data client pytest.
- S-3: StubSlotPort/StubIntentPort/실제 `KisDataHubClient` 경계 mock을 이용한 daemon pytest.
- S-4: 로컬 `env -u PYTHONPATH .venv/bin/pytest` 실행 결과와 import sweep.

# 8. REQ로 넘기기 전 확인 체크

- [x] 범위(In Scope/Out of Scope) 확정 — Issue #3의 기대 동작과 권장 수정안을 그대로 반영.
- [x] 성공 지표의 측정 방식/데이터 출처 확정 — HTTP 호출 stub 및 daemon fixture 기반.
- [x] 영향 리스크 우선순위 및 해소 책임자 지정 — service-dev 구현, qc-reviewer 독립 검증, 팀장 게이트.
- [x] 모든 오픈 질문(OQ) `Open` 상태 없음 확인.
- [x] Freeze 확정 담당자(CONFIRMED_BY) 지정 — 사장님이 dcy-003을 승인.

# 9. 파일 처리 결과

- 처리 결과: 생성
- 생성 경로: docs/discovery/dcy-003_20260720_quote-only-market-data.md
- 참조 문서:
  - docs/srs/Integration/req-003_tvr-decision-cycle-rebalancing.md
  - docs/releases/rel-001_20260711_tvr-m4-constant-mix-v1.md
  - GitHub Issue #3: https://github.com/fourblocks2020/trex-tvr-v1/issues/3
  - `trex_tvr/runtime/daemon.py`, `trex_tvr/market_data/kis_data_hub_client.py`, `trex_tvr/strategies/tvr_strategy.py`

# 10. 사용자 결정 필요 항목 요약

## DECIDE

- 없음. Issue #3이 명시한 quote-only 동작과 `candle_count <= 0` 공통 처리 범위를 Discovery에 반영했다.

## CONFIRM

- 완료. 사장님이 dcy-003의 범위 Freeze와 REQ-003 change request 착수를 승인했다.

## DATA

- 없음. 회귀 검증은 외부 운영 데이터 없이 HTTP/daemon fixture로 수행한다.

# 11. Discovery Freeze

## 섹션 참조

- 범위: [# 5. 요구사항 목록 > 범위 경계](#5-요구사항-목록)
- 리스크/오픈 질문: [# 6. 리스크/가정 목록](#6-리스크가정-목록)
- 성공 기준: [# 7. 초기 성공 기준](#7-초기-성공-기준)
- 인계 전 확인: [# 8. REQ로 넘기기 전 확인 체크](#8-req로-넘기기-전-확인-체크)
- 사용자 결정 필요: [# 10. 사용자 결정 필요 항목 요약](#10-사용자-결정-필요-항목-요약)

## Freeze 플래그

- Handoff Decision: 승인
- Handoff Rationale: 사장님이 dcy-003의 범위를 승인했다. 기존 Implemented REQ-003에 변경 이력을 먼저 기록하고, quote-only 공통 클라이언트 계약의 구현·독립 QC·검증으로 인계한다.
- Ready for REQ Drafting: true
- Confirmed By: 사장님
- Confirmed At (KST): 2026-07-20 13:43:58
