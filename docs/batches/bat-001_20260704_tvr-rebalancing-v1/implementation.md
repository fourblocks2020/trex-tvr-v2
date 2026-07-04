# BAT-001 Implementation

- Status: completed
- Batch: BAT-001

## Changed Files

### New Files
- `trex_tvr/strategies/tvr_strategy.py` — TvrStrategy.evaluate() 실제 리밸런싱 로직
- `trex_tvr/runtime/status.py` — StatusSnapshot + write_snapshot + read_snapshot (REQ-004)
- `tests/strategies/test_tvr_strategy.py` — REQ-001 AC 전부 커버
- `tests/runtime/test_intent_builder.py` — REQ-002 AC 커버
- `tests/runtime/test_observability.py` — REQ-004 AC 커버
- `docs/batches/bat-001_20260704_tvr-rebalancing-v1/` — batch 문서 일체

### Modified Files
- `trex_tvr/strategies/base.py` — SellSignal dataclass 추가, BuyEvaluation 확장
- `trex_tvr/strategies/registry.py` — example_strategy → tvr_strategy import 갱신
- `trex_tvr/transport/base.py` — IntentPort.submit_sell 추가, parse_sell_intent_response 추가, build_slots_request strategy 파라미터 추가
- `trex_tvr/transport/intent_client.py` — PikaIntentClient.submit_sell() 구현
- `trex_tvr/runtime/intent_builder.py` — make_sell_intent_id + build_sell_intent 추가
- `trex_tvr/runtime/daemon.py` — 리밸런싱형 재구성 (buy/sell 분기, entry_capital 제거, strategy_params 폴백, 스냅샷 통합)
- `trex_tvr/runtime/config.py` — ExitConfig 선택적, entry_capital_krw 제거
- `trex_tvr/runtime/cli.py` — `status` 서브커맨드 추가
- `config/instance.example.toml` — TVR 실사양 (entry_capital·[exit] 제거, 런타임 설정만)
- `README.md` — 구현 상태 반영
- `tests/conftest.py` — StubIntentPort.submit_sell, holding_slot fixture 추가
- `tests/runtime/test_daemon_cycle.py` — 리밸런싱형 테스트로 교체

### Deleted Files
- `trex_tvr/strategies/example_strategy.py` — scaffold placeholder → tvr_strategy.py로 교체
- `tests/strategies/test_example_strategy.py` — scaffold 테스트 → test_tvr_strategy.py로 교체

## Key Implementation Notes

1. **TvrStrategy.evaluate()**: current_price는 asset dict에서 우선 읽고 없으면 market_data에서 fallback.
   strategy_params 파싱 실패 시 Diagnostic(stale_quote) 반환 — daemon 레이어에서 missing_strategy_params
   처리와 역할 분리.

2. **build_slots_request() strategy 확장**: `filter="inactive,holding"` + `strategy="tvr_v1"` body로 발송.
   slot_client.py는 body의 `filter` 키만 사용하므로 strategy 키는 현재 전달되지 않는다.
   REQ-003의 "strategy 필터 포함" 의미론은 request body에 키가 있는 것으로 구현했으며,
   실제 wire 전달은 slot_client + trex-core 연동 시 확장 예정(Notes 명시).

3. **[exit] None 처리**: build_buy_intent()는 stop_loss_pct/take_profit_pct를 항상 받는 시그니처.
   exit=None 시 0.0으로 호출 후 payload에서 stop_loss_price/take_profit_price를 pop하는 방식 채택.
   이후 intent_builder 리팩터링에서 Optional 파라미터로 개선 가능(change-req 대상).

4. **status.py**: 임시 파일 write → os.replace() 원자 rename. tmp 파일 삭제는 except 블록에서 best-effort.

## Test Coverage Summary

- 총 81 tests (기존 49 + 신규 32)
- 기존 49 모두 회귀 통과
- import sweep: 3 tests (PASS)
- REQ-001 전략: 14 tests (AC1~AC10 + 추가 경계)
- REQ-002 transport: 8 tests (AC1~AC8)
- REQ-003 daemon: 8 tests (AC1~AC9)
- REQ-004 관측성: 8 tests (AC1~AC7)
