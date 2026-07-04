# BAT-001: TVR v1 리밸런싱 코어 구현

- Status: in-delivery
- Profile: standard
- Source Discovery: dcy-001
- Included REQ: REQ-001, REQ-002, REQ-003, REQ-004
- Excluded REQ: 없음

## Summary

dcy-001(target-value 리밸런싱 전략)의 첫 delivery batch.
스캐폴드 placeholder 상태인 `TvrStrategy.evaluate()`를 실제 constant-mix 리밸런싱 로직으로 교체하고,
매수 전용이었던 transport/daemon을 부분매도 경로까지 갖춘 리밸런싱형으로 확장한다.
day-1 관측성(status/health 스냅샷)도 함께 탑재한다.

## Scope

- In Scope:
  - REQ-001: `SellSignal` dataclass + `TvrStrategy.evaluate()` 실제 리밸런싱 로직 (비중·밴드·dust·whole-share·cash축소·과매도클램프·stale/zero Diagnostic)
  - REQ-002: transport sell-intent 경로 신설 (`IntentPort.submit_sell`, `build_sell_intent`, `parse_sell_intent_response`, `intent_id` 멱등 규약)
  - REQ-003: daemon decision-cycle 리밸런싱형 확장 (slots strategy 필터, inactive+holding 평가, buy/sell 분기, strategy_params 폴백, already_active 재수렴, entry_capital 제거, [exit] 선택화, 현재가 주입)
  - REQ-004: status/health 스냅샷 8항목 JSON (파일 원자 write + CLI `tvr status` 서브커맨드)
  - config/instance.example.toml TVR 실사양 갱신 (entry_capital·[exit] 제거, [strategy.tvr_v1] 런타임 설명)
  - README 반영
  - example_strategy.py → tvr_strategy.py rename(참조 전부 갱신)

- Out of Scope:
  - dcy-002(폴링 케이던스·병렬 worker·다중 인스턴스 팬-인): placeholder 유지
  - trex-core additive wire 변경(strategy 필터·row strategy_params 필드): 별도 repo REQ
  - portfolio-hub strategy_params 노출: 별도 repo REQ
  - e2e wire 검증(fixture 기반 단독 검증만)

## Documents

- Planning: [planning.md](./planning.md)
- Design: [design.md](./design.md)
- Implementation: [implementation.md](./implementation.md)
- Verification: [verification.md](./verification.md)

## Notes

- trex-core slots strategy 필터 + row strategy_params 전달은 현재 tvr 단독 검증에서 stub fixture 기반으로만 커버.
  e2e wire 연동은 trex-core 해당 변경 배포 후 통합 테스트에서 수행(REQ-003 Notes 명시).
- tvr 배포 시 trex-core 미배포 상태에서는 모든 슬롯이 missing_strategy_params 폴백으로 스킵됨(의도된 후방호환).
- bat-001 완료 후 trex-core·portfolio-hub REQ 별도 추적 필요.
