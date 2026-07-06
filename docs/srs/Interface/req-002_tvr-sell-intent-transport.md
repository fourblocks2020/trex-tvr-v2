# req-002: TVR sell-intent 경로 신설

- Status: Implemented
- Type: Interface
- Priority: High
- Owner: service-dev (tvr)

## Intent
- 현재 매수 전용인 transport 레이어에 sell-intent 발신 경로를 추가해, TVR 리밸런싱 부분매도 신호(SellSignal)가 trex-core `SellIntent` wire 계약으로 전달될 수 있도록 한다.
- Discovery dcy-001 FR-4·NFR-2·NFR-3·NFR-4의 sell-intent 경로·시임 유지·멱등성·과매도 방지 요건을 충족하는 transport 계약을 확보한다.
- 시임 규칙(strategies/ → transport 무import) 유지 하에, runtime 레이어만 SellSignal을 소비해 wire 페이로드를 조립·발신하도록 경계를 명확히 한다.

## Requirement
- `trex_tvr/transport/base.py`의 `IntentPort` Protocol에 `submit_sell(payload: dict) -> dict` 메서드를 추가한다. 기존 `submit_buy` 계약은 변경하지 않는다.
- `trex_tvr/transport/intent_client.py`의 `PikaIntentClient`에 `submit_sell(payload: dict) -> dict` 구현을 추가한다. 라우팅 키(`trex.intent.req.<instance_key>`)와 RPC 계약은 buy와 동일하게 사용한다. trex-core `SellIntent` 스키마와 정합하는 페이로드 구조(아래 명세)를 발신한다.
- Sell intent 페이로드 필드 (trex-core `SellIntent` wire 계약):
  - `intent_type`: 리터럴 문자열 `"sell"`
  - `intent_id`: 멱등 키 (아래 규약 참조)
  - `slot_id`: str (필수)
  - `qty`: int (부분매도 수량, 필수, > 0)
  - `reason`: str (필수, 예: `"rebalance_sell"`)
  - `sell_strategy_id`: str (필수, 예: `"tvr_v1"`)
- Sell `intent_id` 규약: `f"sell:{strategy_id}:{trading_date}:{slot_id}"` — buy와 네임스페이스 구분, 하루 한 slot에 한 번의 정규 sell admission. trading_date는 KST YYYY-MM-DD.
- `trex_tvr/runtime/intent_builder.py`에 `build_sell_intent(signal: SellSignal, *, trading_date: str, slot_id: str, strategy_id: str) -> dict` 함수를 추가한다. 반환 dict는 위 페이로드 필드를 모두 포함한다. stop_loss / take_profit / forced_close 필드는 포함하지 않는다(순수 리밸런싱 매도 — 청산 임계 미선언).
- `trex_tvr/transport/base.py`에 sell intent 응답 파서 `parse_sell_intent_response(body: Any) -> dict`를 추가한다. 기존 `parse_intent_response`와 동일한 스키마 검증 로직을 사용하되, sell 페이로드 전용 함수로 분리한다(재사용 가능).
- 시임 규칙 유지: `trex_tvr/strategies/` 아래 모듈은 `transport`, `intent_builder`를 import하지 않는다.

## Acceptance Criteria
1. Given SellSignal(symbol="005930", slot_id="slot-abc", qty=4, reason="rebalance_sell", strategy_id="tvr_v1"), trading_date="2026-07-04", When build_sell_intent() 호출하면, Then dict에 intent_type="sell", intent_id="sell:tvr_v1:2026-07-04:slot-abc", slot_id="slot-abc", qty=4, reason="rebalance_sell", sell_strategy_id="tvr_v1" 포함, stop_loss_price/take_profit_price/forced_close_at 키 없음.
2. Given 동일한 (strategy_id, trading_date, slot_id) 입력, When build_sell_intent()를 2회 이상 호출하면, Then 동일한 intent_id 반환(멱등 키 결정성).
3. Given IntentPort Protocol 체크, When PikaIntentClient 인스턴스를 isinstance(client, IntentPort)로 확인하면, Then True (submit_buy + submit_sell 모두 구현됨).
4. Given stub IntentPort(submit_buy와 submit_sell 모두 구현), When runtime_checkable Protocol isinstance 검사, Then True.
5. Given parse_sell_intent_response({"intent_id": "x", "status": "admitted", "slot_id": "s1"}), When 호출하면, Then {"intent_id": "x", "status": "admitted", "slot_id": "s1", "reason": None} 반환.
6. Given parse_sell_intent_response({"status": "admitted"}), When 호출하면(intent_id 없음), Then WireSchemaError 발생.
7. Given import sweep 테스트, When trex_tvr/strategies/ 아래 모든 .py를 검사하면, Then intent_builder·transport 등 시임 위반 import 없음.
8. Given build_sell_intent(signal=..., qty=0 이하), When 호출하면, Then ValueError 발생(qty 유효성 검사).

## Impacted Area
- Docs: 없음 (interface-contract 문서 미생성, Notes에 wire 계약 가정 명시)
- Modules:
  - `trex_tvr/transport/base.py` — `IntentPort`에 `submit_sell` 추가, `parse_sell_intent_response` 추가
  - `trex_tvr/transport/intent_client.py` — `PikaIntentClient.submit_sell()` 구현
  - `trex_tvr/runtime/intent_builder.py` — `build_sell_intent()` 추가
- Tests:
  - `tests/transport/test_intent_client.py` (신규 또는 확장) — submit_sell stub 테스트, 응답 파서 테스트
  - `tests/runtime/test_intent_builder.py` (신규 또는 확장) — build_sell_intent 필드·멱등키 테스트

## Notes
- **trex-core SellIntent wire 계약 가정**: trex-core가 `intent_type:"sell"`, `slot_id`, `qty`(부분매도), `reason`, `sell_strategy_id` 필드를 수신하는 `SellIntent` 스키마를 구현하고 있다고 가정한다(dcy-001 가정 A-1: 부분매도 지원 기구현). tvr 단독 테스트는 stub으로 진행; e2e wire 검증은 trex-core 배포 후 통합 테스트에서 수행한다.
- **buy와 동일한 RPC 큐**: `trex.intent.req.<instance_key>`. trex-core가 `intent_type` 필드로 buy/sell을 분기한다는 가정이다. 이 계약이 불일치할 경우 구현 시 발견되며 change-req로 처리한다.
- **intent_id 일 1회 규약**: 하루 동일 slot에 대한 sell admission이 2회 발생하면 동일 intent_id로 trex-core dedup 처리된다. 리밸런싱 특성상 동일 거래일 동일 slot에서 재매도가 의미 없으므로 이를 의도된 동작으로 명시한다. 필요 시 change-req로 조정 가능.
- **stop_loss / take_profit 미선언 설계 의도**: TVR은 순수 리밸런싱 전략이다. 매도는 trex-core의 safety-sell이 아니라 TVR이 직접 발신하며, exit 임계는 포함하지 않는다(dcy-001 스코프 정의). 단, trex-core가 HOLDING 슬롯에 기존 SL/TP가 설정된 경우 충돌 가능성이 있다 — 설계 단계에서 확인 필요(설계 인계 사안).

## Change Log

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
- dcy-001 confirmed Discovery의 FR-4(부분매도 경로)·NFR-2(시임)·NFR-3(멱등성)을 근거로 본 REQ 초안을 최초 생성했다(Status: Proposed).

#### Intent
- transport 레이어에 sell-intent 경로를 추가해 TVR 부분매도 신호가 trex-core wire로 전달될 수 있는 계약을 확보한다.

#### Acceptance Criteria Delta
- Added:
  - AC 1~8 신규 추가 (페이로드 필드·멱등키·Protocol·파서·시임·qty 유효성)
- Updated:
  - 없음
- Removed:
  - 없음

#### Impacted Area Delta
- Docs:
  - docs/srs/index.md Register 신규 등록
- Modules:
  - transport/base.py, transport/intent_client.py, runtime/intent_builder.py 수정
- Tests:
  - tests/transport/, tests/runtime/ 확장

#### Delivery Trace Delta
- Batches:
  - none (draft-batch 단계에서 연결)
- Releases:
  - none

#### Revalidation Impact
- Reverification Needed: no
- Existing Implementation Invalidated: no
- Status Recommendation: keep-current
- Follow-up Action: confirm-req에서 승인 전환 검토

#### Validation Plan
- tests/transport/ + tests/runtime/의 sell 경로 단위 테스트 전부 통과 + import sweep 통과로 AC 충족으로 본다.
