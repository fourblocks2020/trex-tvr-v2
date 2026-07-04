# BAT-001 Design

- Status: confirmed
- Batch: BAT-001

## Architecture Summary

스캐폴드의 매수 전용 프레임을 리밸런싱형(매수+부분매도)으로 확장한다.
변경 경계는 4개 레이어로 나뉜다:
1. **strategies/**: SellSignal 추가, TvrStrategy 순수 결정론 로직 구현
2. **transport/**: IntentPort에 submit_sell 추가, sell 페이로드 파서
3. **runtime/intent_builder**: build_sell_intent() 추가 (buy와 병렬 구조)
4. **runtime/daemon + config**: entry_capital 제거, [exit] 선택화, run_decision_cycle 리밸런싱형 재구성, 스냅샷 통합

시임 규칙은 그대로 유지: strategies/ → infra 무import, trex_tvr 전체 → trex_core 무import.

## Changed Areas

- Docs: 없음 (interface-contract 미생성, runtime-flows는 이번 batch 미갱신 — 단일 데몬 구조 불변)
- Modules:
  - `trex_tvr/strategies/base.py` — SellSignal dataclass 추가, BuyEvaluation 확장
  - `trex_tvr/strategies/example_strategy.py` → `trex_tvr/strategies/tvr_strategy.py` (rename)
  - `trex_tvr/strategies/registry.py` — import 참조 갱신
  - `trex_tvr/strategies/__init__.py` — 필요 시 참조 갱신
  - `trex_tvr/transport/base.py` — IntentPort.submit_sell 추가, parse_sell_intent_response 추가, build_slots_request strategy 파라미터 확장
  - `trex_tvr/transport/intent_client.py` — PikaIntentClient.submit_sell() 추가
  - `trex_tvr/runtime/intent_builder.py` — build_sell_intent() 추가, make_sell_intent_id() 추가
  - `trex_tvr/runtime/daemon.py` — run_decision_cycle() 재구성, entry_capital 의존 제거, 스냅샷 통합
  - `trex_tvr/runtime/config.py` — ExitConfig 선택적 처리, entry_capital 제거
  - `trex_tvr/runtime/status.py` (신규) — StatusSnapshot dataclass + 파일 원자 write
  - `trex_tvr/runtime/cli.py` — `status` 서브커맨드 추가
  - `config/instance.example.toml` — TVR 실사양으로 갱신
  - `README.md` — 구현 상태 반영
- Interfaces:
  - `IntentPort` Protocol: `submit_sell(payload: dict) -> dict` 추가 (additive, 기존 submit_buy 불변)
  - `build_slots_request()`: `strategy` 선택 인자 추가 (additive, 기존 `filter` 시그니처 불변)
  - `BuyEvaluation` type alias 확장 (SellSignal 추가, 기존 타입 불변)

## Key Decisions

1. **example_strategy.py → tvr_strategy.py rename (REQ-001)**
   - 근거: CLAUDE.md가 "rename 권장"으로 명시. tvr_strategy.py가 전략 정체성을 명확히 전달.
   - 영향: registry.py·__init__.py·기존 테스트 import 참조 전부 갱신 필요.

2. **status 스냅샷 수단: 파일 원자 write + CLI `tvr status` 서브커맨드 (REQ-004)**
   - 근거:
     - 파일 write는 운영 컨테이너 환경에서 인프라 의존 없이 즉시 접근 가능하고, 원자 write(tmp→rename)로 반쪽 JSON 방지.
     - CLI 서브커맨드는 운영자가 `docker exec ... tvr status`로 즉시 조회 가능해 tsl-v1 교훈 충족.
     - 스냅샷 경로는 환경변수 `TREX_TVR_STATUS_PATH` 또는 기본값 `/tmp/trex_tvr_status.json`.
     - 쓰기 실패는 best-effort (REQ-004 AC-6): 예외를 catch하고 daemon cycle 계속 진행.
   - 대안 제외: HTTP 헬스 엔드포인트는 추가 의존(uvicorn 등)·포트 관리 필요 → day-1 범위 초과.

3. **[exit] Config 선택화 (REQ-003)**
   - 근거: TVR은 exit 임계(stop_loss_pct/take_profit_pct)를 사용하지 않는다(순수 리밸런싱).
     기존 ExitConfig는 frozen dataclass로 required 필드였으나, TVR에서 TOML에 [exit]를 강제하는 것은 불필요한 오설정 위험.
   - 설계: Config에서 `exit: Optional[ExitConfig] = None`으로 변경.
     load_config()에서 [exit] 없으면 None(TVR 정상), 있으면 파싱(타 전략 호환).
     build_buy_intent() 호출 시 exit가 None이면 stop_loss_pct/take_profit_pct를 페이로드에 미포함 (REQ-003 AC: buy payload에 stop_loss_price/take_profit_price 없음).
   - 기존 Daemon 생성자의 `entry_capital_krw > 0` 강제 제거: TVR은 entry_capital 미사용.

4. **entry_capital 제거 (REQ-003)**
   - 근거: TVR은 entry_capital(고정 KRW/슬롯)이 아닌 목표비중(strategy_params.target_weight)이 매수 금액을 결정.
   - 설계: Config.entry_capital_krw 필드 제거, Daemon.__init__에서 entry_capital > 0 검증 제거.
     _slot_for_eval()에서 entry_capital 주입 제거 → qty·cash_balance·current_price·strategy_params 주입.
   - config.py의 load_config()에서 entry_capital_krw 파싱 코드 제거.

5. **build_slots_request() strategy 파라미터 확장 (REQ-003)**
   - 설계: `build_slots_request(filter: str, *, strategy: Optional[str] = None) -> dict` 시그니처 확장.
     strategy가 있으면 body에 `"strategy": strategy` 키 추가.
     기존 `filter`만 있는 경우와 backward-compatible.
   - daemon에서 `build_slots_request(filter="inactive,holding", strategy="tvr_v1")` 호출.
   - 실제 슬롯 요청은 transport.slot_client가 body 그대로 전송. trex-core가 strategy 필터 미지원 시 무시(폴백 경로 = strategy_params 없는 row 스킵).

6. **_eligible_for_eval() 확장 (REQ-003)**
   - 기존: status == "inactive"만 통과.
   - 변경: status in ("inactive", "holding") 통과. inactive = 초기 확립(매수) 후보, holding = 리밸런싱(매수/매도) 후보.
   - _slot_for_eval(): qty, cash_balance, current_price(시세 주입), strategy_params 추가.
   - 현재가 주입: Phase 1 fan-out worker에서 fetch_market_data() 결과의 current_price를 eval_asset에 포함 (candle_count=0 가능).

7. **strategy_params 폴백 처리 (REQ-003)**
   - row에 strategy_params가 없거나 None/빈 dict인 경우: Diagnostic(reason="missing_strategy_params")로 처리, 해당 슬롯 스킵(submit_buy/submit_sell 미호출).
   - 폴백은 trex-core 미배포 기간 한시 동작. 정상 운영은 strategy_params 있는 row 기준.

8. **sell reentry guard: 매 cycle 재평가 (REQ-003)**
   - buy _admitted_slots guard는 buy admission에만 적용 (기존과 동일).
   - sell은 _admitted_slots 체크 없이 매 cycle 재평가. 드리프트가 다음 cycle에도 존재하면 다시 SellSignal 발신 가능.
   - intent_id `sell:{strategy_id}:{trading_date}:{slot_id}` 규약에 의해 같은 날 trex-core에서 dedup.

## Edge Cases

- **qty=0 & cash_balance=0 (zero_allocation)**: Diagnostic(reason="zero_allocation") — 무거래.
- **current_price <= 0 또는 None (stale_quote)**: Diagnostic(reason="stale_quote") — 무거래.
- **dust buy/sell**: qty × current_price < min_trade_krw → HoldDecision(reason="dust_buy"/"dust_sell").
- **over-sell clamp**: 계산된 매도 수량 > 보유 qty → qty로 상한 클램프(과매도 방지).
- **cash 부족 매수 축소**: 계산된 매수 수량 × price > cash_balance → floor(cash_balance/price)로 축소.
- **strategy_params 없는 row**: Diagnostic(reason="missing_strategy_params"), 스킵.
- **already_active 응답**: diagnostics 기록 후 다음 cycle 재수렴(예외 없음, 재시도 없음).
- **스냅샷 write 실패**: best-effort catch + log warning, daemon cycle 중단 없음.
- **sell intent qty <= 0**: ValueError (build_sell_intent 유효성 검사).

## Architecture Impact

- runtime-flows: 기존 매수 전용 흐름에 SellSignal 분기 추가. 단일 cycle 구조는 불변(Phase 1 fan-out + Phase 2 serial 유지). 문서 갱신 대상이나 이번 batch 범위에서는 placeholder 유지(dcy-002 이후 런타임 아키텍처 정렬).
- project-structure: tvr_strategy.py rename, status.py 신규. 갱신 계획 있음(README 반영으로 대체).

## Reference Doc Update Plan

- project-structure.md: README Layout 섹션에 tvr_strategy.py rename + status.py 신규 반영. docs/project-structure.md 문서 현행화는 batch-lit 범위 이후로 deferred.
- runtime-flows.md: 매수/매도 분기 추가 반영 대상이나 dcy-002 케이던스 설계 후 일괄 갱신 예정 — 이번 batch에서는 갱신 보류.
