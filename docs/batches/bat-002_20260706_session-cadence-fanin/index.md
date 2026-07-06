# BAT-002: 세션 캘린더 + 케이던스 + 다중 인스턴스 팬-인

- Status: in-delivery
- Profile: standard
- Source Discovery: dcy-002
- Included REQ: REQ-005, REQ-006, REQ-007
- Excluded REQ: 없음

## Summary

dcy-002(실행 케이던스·슬롯 패치·팬-인) delivery batch.
- REQ-005: 08:00~20:00 NXT 세션 구조 인식 + 슬롯별 markets 게이트
- REQ-006: 60s 바닥 poll_interval + 무슬립 드레인
- REQ-007: config instances 목록 + 팬-인 수집·병합·intent 라우팅 + intent_id 인스턴스 포함

## Scope

- In Scope:
  - REQ-005: `runtime/session_calendar.py` 신설 (SessionWindow 열거형, get_session, is_tradeable, is_nxt_only), config session_start/session_end 추가, daemon decision-cycle에 세션 게이트 + NXT markets 게이트 통합
  - REQ-006: config poll_interval=60 추가, daemon run_forever 드레인/바닥 루프 재구현 (monotonic 기반), 고정 슬립 코드 제거, instance.example.toml 문서화
  - REQ-007: config instances: list[str] 추가, 팬-인 수집·병합·라우팅, intent_id에 instance_key 포함 (buy/sell 모두), intent_builder.py 확장, slot_client/intent_client 인스턴스별 팩토리 지원
  - 설계 인계 사항 design.md 명시: OQ-3 Deferred, in-flight 이전 방안, kdh NX/UN 미배포 폴백, 큐 네이밍 규약

- Out of Scope:
  - OQ-3 연장세션 보호장치(스프레드 가드·지정가 폭·세션별 밴드 가중) — 초기 운영 관찰 후 change-req
  - trex-core 인스턴스별 watchdog 실물 검증 — e2e wire 배포 후
  - kdh NX/UN 현재가 API 배포 — 별도 kdh REQ
  - portfolio-hub markets 배관 (wire 배관) — REQ-116/055 소관 타 repo

## Documents

- Planning: [planning.md](./planning.md)
- Design: [design.md](./design.md)
- Implementation: [implementation.md](./implementation.md)
- Verification: [verification.md](./verification.md)

## Notes

- REQ-005의 markets 게이트는 tvr 소비 쪽 계약만 구현. wire 배관(portfolio-hub→trex-core→tvr) 미배포 시 markets=[] → 보수 해석(["KRX"])으로 폴백 — NXT 전용 구간 intent 억제.
- REQ-007의 intent_id 포맷 변경(`{strategy}:{instance}:{date}:{slot}`)은 기존 in-flight intent와 충돌 가능. 배포 시 admitted 초기화로 충분한지 design.md에서 평가.
- kdh NX/UN 현재가 미배포 시 NXT 전용 구간 fetch 실패 → 해당 cycle HOLD 폴백(슬롯 Diagnostic reason="quote_unavailable").
