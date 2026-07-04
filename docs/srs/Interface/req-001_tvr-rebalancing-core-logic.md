# req-001: TVR v1 리밸런싱 평가 코어 로직

- Status: Approved
- Type: Interface
- Priority: High
- Owner: service-dev (tvr)

## Intent
- `TvrStrategy.evaluate()`를 순수 결정론 함수로 구현해, 슬롯 row(qty·cash_balance·현재가·strategy_params)를 입력받아 아이템별 현재 주식비중을 계산하고 드리프트가 밴드를 초과할 때 정확한 매수/부분매도/홀드 신호를 산출한다.
- 전략 로직을 인프라와 완전히 분리된 순수 결정 단위로 고립시켜, 시임 규칙을 유지하면서 임의 fixture로 단위 테스트가 가능한 계약을 확보한다.
- Discovery dcy-001 FR-2·FR-3·FR-4·FR-5의 핵심 결정 로직을 단일 평가 함수에 집결시킨다.

## Requirement
- `trex_tvr/strategies/base.py`에 `SellSignal` dataclass를 추가한다. 필드: `symbol: str`, `slot_id: str`, `qty: int`(부분매도 수량), `reason: str`, `strategy_id: str`. 기존 `BuySignal`·`HoldDecision`·`Diagnostic` 계약은 변경하지 않는다.
- `BuyEvaluation` 타입 alias를 `Union[BuySignal, SellSignal, HoldDecision, Diagnostic]`으로 확장한다.
- `trex_tvr/strategies/example_strategy.py`의 `TvrStrategy.evaluate()`를 아래 규칙에 따른 실제 리밸런싱 로직으로 교체한다.
  - 입력 asset dict에서 `qty`(보유수량), `cash_balance`(KRW), `current_price`(KRW), `strategy_params.target_weight`(0 < float < 1), `strategy_params.rebalance_band_pct`(float), `strategy_params.min_trade_krw`(int) 를 읽는다.
  - 주식가치 = qty × current_price. 총할당 = 주식가치 + cash_balance.
  - 현재 주식비중 = 주식가치 / 총할당 (총할당 = 0이면 Diagnostic `zero_allocation`).
  - 드리프트 = 현재비중 - target_weight. |드리프트| ≤ rebalance_band_pct/100 이면 `HoldDecision(reason="within_band")`.
  - 부족(드리프트 < -band): 목표주식가치 = target_weight × 총할당. 델타금액 = 목표주식가치 - 주식가치. 매수수량 = floor(델타금액 / current_price). cash 부족 시 매수수량을 floor(cash_balance / current_price)로 축소. 매수수량 ≤ 0 이거나 매수수량 × current_price < min_trade_krw이면 HoldDecision(reason="dust_buy"). 아니면 BuySignal(target_qty=매수수량, entry_price=current_price, ...).
  - 초과(드리프트 > +band): 목표주식가치 = target_weight × 총할당. 델타금액 = 주식가치 - 목표주식가치. 매도수량 = floor(델타금액 / current_price). 매도수량 > qty면 qty로 상한 클램프(과매도 방지). 매도수량 ≤ 0 이거나 매도수량 × current_price < min_trade_krw이면 HoldDecision(reason="dust_sell"). 아니면 SellSignal(qty=매도수량, ...).
  - current_price ≤ 0 또는 비정상이면 Diagnostic(reason="stale_quote").
  - 아이템별 독립 — 다른 아이템의 cash나 qty를 참조하지 않는다.
- `evaluate()`는 IO/clock/random 없는 순수 결정론적 함수다. `strategies/` 모듈은 `transport`, `market_data`, `redis`, `pika`, `httpx`, `trex_core`를 import하지 않는다(import sweep 유지).

## Acceptance Criteria
1. Given qty=10, cash_balance=500_000, current_price=50_000, target_weight=0.70, rebalance_band_pct=5.0, min_trade_krw=10_000, When evaluate() 호출하면, Then 현재비중 = 10×50000/(10×50000+500000) = 50%, |드리프트| = 20pp > 5pp → BuySignal(target_qty=4) 반환 (목표주식가치=700000, 델타=200000, floor(200000/50000)=4, 4×50000=200000≥10000).
2. Given qty=10, cash_balance=500_000, current_price=50_000, target_weight=0.70, rebalance_band_pct=5.0, min_trade_krw=10_000, cash_balance에서 매수에 필요한 200_000 KRW를 현금이 100_000뿐(cash_balance=100_000)인 경우, When evaluate(), Then 매수수량=floor(100000/50000)=2인 BuySignal(target_qty=2) 반환(현금 부족 축소).
3. Given qty=15, cash_balance=300_000, current_price=50_000, target_weight=0.70, rebalance_band_pct=5.0, min_trade_krw=10_000, When evaluate(), Then 현재비중=75%×50000/(750000+300000)≈71.4% → |드리프트|≈1.4pp ≤ 5pp → HoldDecision(reason="within_band").
4. Given qty=20, cash_balance=100_000, current_price=50_000, target_weight=0.70, rebalance_band_pct=5.0, min_trade_krw=10_000, When evaluate(), Then 현재비중=20×50000/(1000000+100000)≈90.9% → 초과(드리프트≈20.9pp > 5pp) → SellSignal(qty=floor((909090-700000×(1100000/1100000))/50000)=floor(계산결과) — 즉, 목표주식가치=0.70×1100000=770000, 매도델타=1000000-770000=230000, floor(230000/50000)=4, qty_clamp: 4≤20 → SellSignal(qty=4)).
5. Given 매도수량 계산 결과가 1이고 1×current_price < min_trade_krw인 경우, When evaluate(), Then HoldDecision(reason="dust_sell").
6. Given current_price=0 또는 None, When evaluate(), Then Diagnostic(reason="stale_quote").
7. Given 총할당=0(qty=0, cash_balance=0), When evaluate(), Then Diagnostic(reason="zero_allocation").
8. Given qty=20, 계산된 부분매도 수량이 qty+5=25인 경우, When evaluate(), Then qty로 클램프된 SellSignal(qty=20) 반환(과매도 방지).
9. Given 동일한 입력을 2회 호출하면, Then 항상 동일한 출력(결정성).
10. Given import sweep 테스트, When trex_tvr/strategies/ 아래 모든 .py를 검사하면, Then transport·market_data·redis·pika·httpx·trex_core import가 없음.

## Impacted Area
- Docs: 없음 (baseline 문서 미생성 상태, 갱신 대상 없음)
- Modules:
  - `trex_tvr/strategies/base.py` — `SellSignal` dataclass 추가, `BuyEvaluation` 확장
  - `trex_tvr/strategies/example_strategy.py` — `TvrStrategy.evaluate()` 실 로직으로 교체
- Tests:
  - `tests/strategies/test_tvr_strategy.py` (신규) — 경계·dust·반올림·현금부족·과매도 방지·결정성 케이스

## Notes
- **밴드 폭 및 min_trade_krw 기본값은 placeholder**: dcy-001 OQ-1·OQ-2는 설계 시뮬(6.5년 일봉 회전율·비용 곡선) 확정 후 REQ 갱신 예정. 현재 REQ는 "파라미터로 받는 구조"만 확정하며, 실제 운영 기본값은 설계 단계에서 change-req로 보완한다.
- **초기 확립(전량 cash) 처리**: qty=0, cash_balance>0인 경우 현재비중=0, 드리프트=-target_weight로 자연스럽게 BuySignal 경로를 탄다. dcy-001 OQ-3(분할 진입 여부)는 이 REQ에서 "한 번에 target까지" 규칙으로 처리하며, 분할 진입은 v2 대상이다.
- **strategy_params 소스**: 실제 운용 시 strategy_params는 trex-core 슬롯 row에서 전달받는다(REQ-003 wire 계약 의존). 단위 테스트는 fixture dict로 직접 주입.
- NFR-2(시임 규칙)·NFR-3(결정성)은 AC-9·AC-10으로 본 REQ에 흡수. 별도 NFR REQ 미승격.

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
- dcy-001 confirmed Discovery의 FR-2·FR-3·FR-4·FR-5를 근거로 본 REQ 초안을 최초 생성했다(Status: Proposed).

#### Intent
- TVR 리밸런싱 평가 로직을 순수 결정론 계약으로 명세해 구현 가능한 요구사항 단위를 확보한다.

#### Acceptance Criteria Delta
- Added:
  - AC 1~10 신규 추가 (경계·dust·반올림·현금 부족·과매도 방지·결정성·import sweep)
- Updated:
  - 없음
- Removed:
  - 없음

#### Impacted Area Delta
- Docs:
  - docs/srs/index.md Register 신규 등록
- Modules:
  - trex_tvr/strategies/base.py, trex_tvr/strategies/example_strategy.py 신규/수정
- Tests:
  - tests/strategies/test_tvr_strategy.py 신규

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
- tests/strategies/test_tvr_strategy.py의 경계·dust·반올림·현금 부족·과매도 방지·결정성 케이스 전부 통과 + import sweep 통과로 AC 충족으로 본다.
