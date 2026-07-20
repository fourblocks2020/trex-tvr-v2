# req-003: TVR decision-cycle 리밸런싱형 확장 + slots strategy 필터

- Status: Approved
- Type: Integration
- Priority: High
- Owner: service-dev (tvr)

## Intent
- 현재 매수 전용인 `runtime/daemon.py` decision-cycle을 리밸런싱형으로 확장해, BuySignal과 SellSignal을 모두 처리하고 slots 요청 시 `strategy:"tvr_v1"` 필터를 포함해 자기 전략 슬롯만 수신한다.
- trex-core의 strategy 필터·row strategy_params 전달은 **타 repo additive wire 변경(계약 가정)**이므로, tvr 데몬 내부는 fixture 기반으로 단독 검증하고, e2e wire 검증은 trex-core 배포 후 수행한다.
- Discovery dcy-001 FR-2·FR-3·FR-4·FR-6과 NFR-4의 엔진 레벨 요건을 충족한다.
- dcy-003의 확정 범위에 따라 TVR quote-only 평가 경로는 분봉을 요청하지 않고 현재가만 조회한다.

## Requirement
- `trex_tvr/transport/base.py`의 `SlotPort.request_slots(filter: str)` 시그니처를 유지하되, `filter` 인자에 `"strategy:tvr_v1"` 값을 전달할 수 있도록 `build_slots_request()`가 strategy 파라미터를 수용하도록 확장한다. 구체 방안: `filter` 문자열 포맷을 `"strategy:tvr_v1"` 단일 문자열로 유지하거나, slots 요청 body에 별도 `strategy` 키를 추가하는 방식을 선택한다. 선택 기준: trex-core wire 계약과 정합. 설계 단계에서 확정하며, 이 REQ는 "strategy 필터를 slots 요청에 포함한다"는 의미론을 확정한다.
- `runtime/daemon.py`의 `run_decision_cycle()`을 리밸런싱형으로 재구성한다:
  - slots 요청 시 `strategy:"tvr_v1"` 필터 파라미터 포함 (위 SlotPort 확장 연동).
  - 슬롯 row 필터링 기준 변경: 기존 `status == "inactive"` 단독 필터에서, TVR은 `status in ("inactive", "holding")` 슬롯을 모두 평가 대상으로 한다. inactive는 초기 확립(매수) 후보, holding은 리밸런싱(매수/매도) 후보.
  - 슬롯 row에서 `strategy_params`(dict)를 추출해 `eval_asset`에 포함한다. trex-core가 strategy_params를 전달하지 않는 경우(필터 미지원 서버, 구 버전): 데몬 측 폴백 — strategy_params가 없는 row는 `Diagnostic(reason="missing_strategy_params")`로 처리하고 스킵(매수/매도 무시).
  - Phase 2(직렬 intent 발신): BuySignal이면 `build_buy_intent()` + `submit_buy()`, SellSignal이면 `build_sell_intent()` + `submit_sell()`. buy intent에 stop_loss_price / take_profit_price / forced_close_at 미선언(순수 리밸런싱 — 템플릿의 [exit] config 상속 제거).
  - in-flight 처리: `submit_buy()`/`submit_sell()` 응답이 `status == "rejected"`, `reason == "already_active"`이면 해당 slot을 diagnostics에 기록하고 다음 cycle 재수렴을 기다린다(재시도 없음).
  - 기존 same-day reentry guard(`_admitted_slots`)는 buy admission에만 적용한다. sell은 매 cycle 재평가(드리프트가 다음 cycle에도 존재하면 다시 발신 가능).
- `Daemon.__init__`에서 `entry_capital_krw` 의존성을 제거한다. TVR은 entry_capital이 아닌 목표비중(strategy_params.target_weight)이 금액을 결정한다. 대신 슬롯 row의 `cash_balance`를 평가 입력으로 사용한다.
- `_slot_for_eval(row)` 메서드를 갱신해 `qty`, `cash_balance`, `current_price`(시세 주입 — 다음 항목 참조), `strategy_params`를 eval_asset에 포함한다.
- Phase 1(fan-out) 워커는 `market_data_client.fetch_market_data(code, now_kst=..., candle_count=0)`로 TVR 평가용 시세를 획득하고 eval_asset에 `current_price`를 주입한다. `candle_count <= 0`인 quote-only 경로는 현재가 조회만 수행하고 분봉 endpoint를 호출하지 않으며 `candles=[]`를 반환한다. `candle_count > 0`인 기존 분봉 조회·당일 필터링 동작은 유지한다.
- `config.py`에서 `[exit]` 섹션(stop_loss_pct, take_profit_pct) 의존을 TVR 데몬 config에서 제거하거나 선택적으로 만든다. TVR은 exit 임계를 사용하지 않는다(순수 리밸런싱).
- **후방호환 폴백 규칙**: trex-core가 `strategy` 필터를 미지원해 전체 슬롯을 반환하는 경우, 데몬은 row의 `strategy_params` 존재 여부로 TVR 대상 슬롯인지 판별하고, strategy_params가 없는 슬롯은 Diagnostic으로 스킵한다. 이 폴백은 배포 전환 기간 한시 동작이며, 정상 운영은 strategy 필터 지원 trex-core 기준이다.
- dcy-002(폴링 케이던스·병렬 worker·다중 인스턴스 팬-인·백프레셔) 소관 사안은 이 REQ에서 "단일 데몬·기존 고정 폴링 placeholder" 가정 유지. dcy-002 이후 change-req로 보완한다.

## Acceptance Criteria
1. Given stub SlotPort가 `strategy:"tvr_v1"` 필터 파라미터를 검사하도록 구성된 경우, When run_decision_cycle() 호출 시, Then SlotPort.request_slots() 호출 인자에 strategy 필터가 포함됨(stub 검증).
2. Given stub SlotPort가 status="inactive", strategy_params={"target_weight":0.7,"rebalance_band_pct":5.0,"min_trade_krw":10000}인 row를 반환하고 TvrStrategy가 BuySignal을 반환하는 경우, When run_decision_cycle(), Then submit_buy() 한 번 호출됨, buy payload에 stop_loss_price / take_profit_price 키 없음.
3. Given stub SlotPort가 status="holding", strategy_params 포함 row를 반환하고 TvrStrategy가 SellSignal을 반환하는 경우, When run_decision_cycle(), Then submit_sell() 한 번 호출됨.
4. Given stub SlotPort가 strategy_params 없는 row를 반환하는 경우, When run_decision_cycle(), Then 해당 row에 대해 submit_buy/submit_sell 미호출, diagnostics에 reason="missing_strategy_params" 포함.
5. Given submit_buy() 응답이 {"status":"rejected","reason":"already_active"}, When run_decision_cycle(), Then slot을 diagnostics에 기록하고 다음 cycle 재수렴 처리(재시도 없음, 예외 없음).
6. Given submit_sell() 응답이 {"status":"rejected","reason":"already_active"}, When run_decision_cycle(), Then 동일하게 diagnostics 기록, 예외 없음.
7. Given stub SlotPort가 degraded=True를 반환하는 경우, When run_decision_cycle(), Then status="degraded_hold" 반환, submit_buy/submit_sell 미호출.
8. Given 동일 slot_id가 이전 cycle에서 buy로 admitted된 경우, When 다음 cycle에서 동일 slot이 inactive로 다시 나타나면, Then reentry guard 적용(buy 재발신 없음). holding 슬롯 sell은 매 cycle 재평가 대상.
9. Given Daemon 생성 시 entry_capital_krw 설정 없이도 ConfigError 미발생(또는 TVR 전용 config에서 해당 필드 선택적 처리).
10. Given `fetch_market_data(..., candle_count=0)` 호출, When quote 조회가 성공하면, Then minute-candle endpoint는 호출되지 않고 반환 데이터의 `current_price`는 quote 가격이며 `candles=[]`이다.
11. Given TVR quote-only 경로에서 BuySignal을 반환하는 stub strategy와 eligible slot, When `run_decision_cycle()` 호출, Then 분봉 endpoint 요청 없이 buy intent가 한 번 제출된다.

## Impacted Area
- Docs: 없음 (interface-contract 미생성; 타 repo wire 계약 영향은 Notes에 명시)
- Modules:
  - `trex_tvr/transport/base.py` — `build_slots_request()` strategy 파라미터 확장
  - `trex_tvr/market_data/kis_data_hub_client.py` — `candle_count <= 0` quote-only 분기
  - `trex_tvr/runtime/daemon.py` — `run_decision_cycle()` 리밸런싱형 재구성, `_eligible_for_eval()`, `_slot_for_eval()` 갱신, `entry_capital` 의존 제거
  - `trex_tvr/runtime/config.py` — `[exit]` 섹션 선택적 처리
- Tests:
  - client/daemon 회귀 테스트 — quote-only에서 minute-candle endpoint 미호출 및 BuySignal buy intent 제출

## Notes
- **타 repo wire 계약 가정 (계약 의존 명시)**:
  - ★ trex-core `trex.slots.req`에 `strategy:"tvr_v1"` 필터 파라미터를 지원하는 것은 trex-core **additive wire 변경(미구현)** 이다. tvr 단독 검증은 stub fixture 기반으로 수행하며, e2e wire 연동은 trex-core 해당 변경 배포 후 통합 테스트에서 검증한다.
  - ★ trex-core 슬롯 row에 `strategy_params`(JSON dict) 필드 전달 역시 trex-core additive 변경(미구현)이다. 현재 슬롯 row에 해당 필드가 없으므로, tvr 배포 후 trex-core 미배포 상태에서는 모든 슬롯이 missing_strategy_params 폴백으로 스킵됨을 명시한다.
  - 위 두 trex-core 변경에 대한 별도 tvr-외 REQ(trex-core repo)가 필요하다.
- **폴링 간격·세션 윈도우·병렬 worker**: dcy-002 소관. 이 REQ는 "기존 placeholder 유지"로 명시하며 dcy-002 완료 후 change-req 보완.
- **TVR에서 [exit] config 제거**: 템플릿 기반 코드의 `ExitConfig`(stop_loss_pct, take_profit_pct)는 TVR에서 불필요하다. 설계 단계에서 config 구조를 정리하고 tvr 전용 Config 분기 또는 선택적 필드로 변경한다. 기존 template 코드와의 호환성은 설계 인계 사안.
- **시세 획득 인터페이스**: TVR은 분봉 candles 불필요 — `current_price`만 필요. quote-only는 `fetch_market_data(..., candle_count=0)`가 `get_quote()`만 호출해 반환한 `current_price`를 사용하며, 이 결정은 dcy-003/CHG-20260720-01로 확정했다.

## Change Log

### CHG-20260720-02
- Date: 2026-07-20
- Author: 팀장(Claude) — confirm-req

#### Change Summary
- CHG-20260720-01의 quote-only 분봉 요청 제거 범위와 AC-10/11을 검토하여 `Approved`로 전환한다 (REQ-003 revision 2).

#### Acceptance Criteria Delta
- Added/Updated/Removed: 없음. CHG-20260720-01의 AC-10/11을 승인 delivery input으로 확정.

#### Delivery Trace Delta
- BAT-003이 REQ-003@2의 단일 표준 delivery batch가 된다. 구현·독립 QC·verification·confirm-req-implemented 증거가 있어야 다음 release 초안을 평가할 수 있다.

#### Revalidation Impact
- Reverification Needed: yes / Existing Implementation Invalidated: yes.
- Status Recommendation: Approved.

#### Validation Plan
- CHG-20260720-01의 MockTransport·daemon 회귀·전체 pytest·독립 QC 계획을 BAT-003 verification에 AC별로 매핑한다.

### CHG-20260720-01
- Date: 2026-07-20
- Author: 팀장(Claude) — dcy-003 confirmed 범위에 따른 change-req 초안

#### Change Summary
- 기존 `fetch_market_data(..., candle_count=0)`가 quote 조회 뒤에도 `/v1/candles/minute?count=0`을 호출해 TVR decision cycle을 HTTP 오류로 중단시키는 결함을 보정한다. dcy-003 confirmed 범위에 따라 quote-only 경로의 분봉 요청을 금지한다.

#### Intent
- TVR이 현재가만 필요로 하는 평가에서 quote-only 시세를 정상 취득하여 BuySignal/SellSignal 평가와 intent 제출을 계속할 수 있게 한다. 전략 산식·AMQP wire·kis-data-hub API 계약은 변경하지 않는다.

#### Acceptance Criteria Delta
- Added: AC-10 (quote-only minute-candle endpoint 미호출 및 `candles=[]`), AC-11 (quote-only BuySignal → buy intent 회귀).
- Updated: Requirement의 시세 획득 인터페이스를 quote-only 분기 규칙으로 명확화.
- Removed: 없음.

#### Impacted Area Delta
- Modules: `trex_tvr/market_data/kis_data_hub_client.py`, 회귀 검증상 `trex_tvr/runtime/daemon.py` 호출 경로.
- Tests: client mock-transport 테스트와 daemon decision-cycle quote-only 회귀 테스트.
- Docs: dcy-003 및 후속 BAT-003/REL 초안 trace.

#### Delivery Trace Delta
- Previous Batch: BAT-001의 REQ-003 구현·검증 증거는 quote-only 실패 경로를 다루지 않아 현재 결함에 대한 증거로 사용할 수 없다.
- New Delivery: BAT-003을 별도 표준 batch로 편성해 구현, 독립 QC, verification 및 REQ 재동기화를 수행한다.
- Release Impact: REL-001을 수정하지 않는다. BAT-003 release-candidate가 확보된 후 별도 release 초안으로 평가한다.

#### Revalidation Impact
- Reverification Needed: yes / Existing Implementation Invalidated: yes.
- Status Recommendation: Proposed. change-req 및 confirm-req 후 Approved로 전환하고 BAT-003에서 재구현·재검증한다.

#### Validation Plan
- MockTransport로 `candle_count=0`의 quote-only 요청에서 quote endpoint만 호출되는지 검증한다.
- daemon 회귀 테스트에서 BuySignal/eligible slot이 quote-only market data를 통해 buy intent 한 건을 제출하는지 검증한다.
- 전체 pytest와 독립 QC에서 기존 `candle_count>0` 분봉·당일 필터링 동작 회귀가 없는지 확인한다.

### CHG-20260704-04
- Date: 2026-07-04
- Author: 팀장(Claude) — confirm-req-implemented

#### Change Summary
- Approved → **Implemented**: bat-001 구현 완료. 독립 QC PASS-WITH-NOTES(blocker 0, MAJOR 1건 수정 반영: REQ-003 AC-1 strategy 필터 wire 전달), 88 passed. PR #1 사장님 결재·머지(1eeac8d, 2026-07-04) = confirm-batch-verification 완료.
- 검증 범위: fixture 기반 단독 검증. e2e(trex-core slots strategy 필터·portfolio-hub strategy_params 노출)는 타 repo REQ 배포 후 별도 확인.

#### Acceptance Criteria Delta
- Added/Updated/Removed: 없음

#### Delivery Trace Delta
- Batches: bat-001_20260704_tvr-rebalancing-v1 (merged, PR #1)

#### Revalidation Impact
- Reverification Needed: no / Existing Implementation Invalidated: no
- Status Recommendation: Implemented

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
- dcy-001 confirmed Discovery의 FR-2·FR-3·FR-4·FR-6을 근거로 본 REQ 초안을 최초 생성했다(Status: Proposed).

#### Intent
- decision-cycle을 리밸런싱형으로 확장하고 strategy 필터를 추가해 TVR 매수/매도 신호가 모두 처리되는 엔진 계약을 확보한다.

#### Acceptance Criteria Delta
- Added:
  - AC 1~9 신규 추가
- Updated:
  - 없음
- Removed:
  - 없음

#### Impacted Area Delta
- Docs:
  - docs/srs/index.md Register 신규 등록
- Modules:
  - transport/base.py, runtime/daemon.py, runtime/config.py 수정
- Tests:
  - tests/runtime/test_daemon.py 신규/확장

#### Delivery Trace Delta
- Batches:
  - none (draft-batch 단계에서 연결)
- Releases:
  - none

#### Revalidation Impact
- Reverification Needed: no
- Existing Implementation Invalidated: no
- Status Recommendation: keep-current
- Follow-up Action: confirm-req에서 승인 전환 검토; trex-core wire 변경 REQ 별도 추적

#### Validation Plan
- tests/runtime/test_daemon.py의 stub 기반 리밸런싱 cycle 시나리오 전부 통과로 AC 충족으로 본다. e2e wire 검증은 trex-core 배포 후 통합 테스트.
