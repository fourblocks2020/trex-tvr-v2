# BAT-001 Planning

- Status: confirmed
- Batch: BAT-001
- Profile: standard

## Included REQ

- REQ-001: TVR v1 리밸런싱 평가 코어 로직 (Interface)
- REQ-002: TVR sell-intent 경로 신설 (Interface)
- REQ-003: TVR decision-cycle 리밸런싱형 확장 + slots strategy 필터 (Integration)
- REQ-004: TVR status/health 관측성 day-1 (Non-Functional)

## Delivery Plan

### 구현 순서

1. **전략 레이어 (REQ-001)**: `strategies/base.py`에 `SellSignal` 추가 + `BuyEvaluation` 확장 →
   `strategies/example_strategy.py` → `tvr_strategy.py` rename + `TvrStrategy.evaluate()` 실제 리밸런싱 로직 구현.
   registry·기존 import 참조 전부 갱신.

2. **transport sell 경로 (REQ-002)**: `transport/base.py`에 `IntentPort.submit_sell` + `parse_sell_intent_response` 추가 →
   `transport/intent_client.py`에 `PikaIntentClient.submit_sell()` 구현 →
   `runtime/intent_builder.py`에 `build_sell_intent()` 추가.

3. **daemon 리밸런싱형 확장 (REQ-003)**: `runtime/daemon.py` run_decision_cycle() 재구성
   (inactive+holding 평가, strategy 필터, buy/sell 분기, strategy_params 폴백, entry_capital 제거, [exit] 선택화) →
   `transport/base.py` `build_slots_request()` strategy 파라미터 확장 →
   `runtime/config.py` [exit] 선택적 처리 + entry_capital 제거.

4. **관측성 (REQ-004)**: `runtime/status.py` 신규 (스냅샷 원자 write) →
   daemon에 스냅샷 업데이트 통합 → CLI `status` 서브커맨드 추가.

5. **config 갱신 + README 반영**: `config/instance.example.toml` TVR 실사양으로 업데이트.
   README의 scaffold TODO 노트를 실제 구현 상태로 업데이트.

6. **테스트 작성**: REQ-001~004 AC 전부 커버하는 단위테스트.
   기존 테스트 갱신(rename 반영, entry_capital 의존 제거).

### 선행 조건

- REQ-001~004 모두 Approved (확인됨)
- 스캐폴드 베이스라인 커밋(c6767b7) 기준 — 베이스라인 pytest 49 passed 확인됨

## Dependencies

- Internal:
  - `strategies/base.py` → `strategies/tvr_strategy.py` (REQ-001 완료 후 REQ-002·003 테스트 작성 가능)
  - `transport/base.py` submit_sell + `runtime/intent_builder.py` build_sell_intent (REQ-002) → `runtime/daemon.py` (REQ-003) sell 분기 의존
  - `runtime/status.py` (REQ-004) → `runtime/daemon.py` 통합
- External:
  - trex-core: strategy 필터·row strategy_params 미구현 → stub fixture로 단독 검증, e2e는 추후
  - portfolio-hub: strategy_params 노출 미구현 → 동일

## Design Gate

- Design Required Before Implementation: yes
- Reason:
  - REQ-003의 daemon 대규모 재구성(entry_capital 제거, [exit] 선택화, buy/sell 분기, strategy_params 폴백)은
    Config dataclass 변경 + Daemon 생성자 계약 변경을 수반해 기존 테스트와 fixture에 영향을 준다.
    변경 경계(Config 구조·_eligible_for_eval·_slot_for_eval 계약·관측성 모듈 배치)를 설계 문서에 확정하고 나서 구현한다.
  - REQ-004의 status 스냅샷 수단(파일 write + CLI)을 설계 문서에서 결정하고 구현에 반영한다.

## Milestones

1. REQ-001 전략 레이어 완료 (SellSignal + TvrStrategy.evaluate() + import sweep 통과)
2. REQ-002 transport sell 경로 완료 (build_sell_intent + submit_sell + 파서)
3. REQ-003 daemon 리밸런싱형 완료 (run_decision_cycle + config 재구성)
4. REQ-004 관측성 완료 (status.py + CLI status 서브커맨드)
5. 전체 pytest 통과 + draft-batch-verification 완료

## Risks

- Config 구조 변경(entry_capital 제거, [exit] 선택화): 기존 테스트·fixture가 entry_capital을 직접 주입하므로 전부 갱신 필요.
  완화: 설계 단계에서 변경 경계를 명확히 확정 후 구현.
- trex-core wire 미구현: strategy_params 없는 row → 폴백 Diagnostic로 스킵. tvr 단독 동작은 stub 기반 보장.
  완화: 폴백 경로 단위 테스트(AC-4) 명시적 검증.
- import sweep: tvr_strategy.py 이름 변경 + 새 모듈 추가 시 시임 규칙 위반 위험.
  완화: 모든 변경 후 import sweep 테스트 실행을 구현 완료 게이트로 삼음.
