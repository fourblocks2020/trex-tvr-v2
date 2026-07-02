# 0. 문서 상태

- 상태: draft
- 문서 ID: dcy-001_20260702_target-value-rebalancing
- 이슈명: target-value(constant-mix) 목표비율 리밸런싱 전략 v1
- 작성 시각(KST): 2026-07-02
- 마지막 갱신 시각(KST): 2026-07-02 (v2: 전략설정=portfolio-hub strategy_sets 확정, name="tvr_v1" 확정, slots strategy 필터 추가, A-1 갱신)
- 대체됨: 없음
- 후속 Discovery 참조: 없음 (v2 = 목표비중 동적조절은 별도 Discovery 예정)
- 생성된 REQ 참조: 없음

# 1. 계획 상태 요약

- 연계 문서 현황:
	- 관련 점수 파일: 미생성
	- 관련 TODO/메모: GitHub 보드 #7 "★M4 메인: target-value 장기 리밸런싱 전략 신규" (Priority P0, Owner 팀장)
- 해석:
	- 신규 장기전략 target-value를 M4 메인 산출물로 추가. repo `trex-tvr-v1`는 strategy-template 복사 + stage-pilot baseline로 스캐폴드 완료(커밋 c6767b7). 본 Discovery가 첫 real Discovery.
	- 이번 Discovery에서 판단할 핵심: **고정 per-종목 목표비중을 자동 리밸런싱으로 유지하는 v1의 범위 경계** — 어디까지가 v1이고(고정목표 유지), 무엇이 v2인가(목표비중 동적조절).

# 2. 요구사항 판정 결과

- 판정: 신규
- 근거
	- trex-orb-v1(돌파 진입)·trex-tsl-v1(트레일링 청산)과 완전히 다른 전략 클래스(포트폴리오 목표비율 리밸런싱). 기존 REQ 없음.
	- 사장님이 과거 유사 로직(cash vs 보유시가 비율 맞추기)을 구현한 적 있으나 TREX 아키텍처(trex-core wire·장기 인스턴스) 위 신규 데몬으로 이식하는 것이라 신규.

# 3. 문제점의 요약

- 현재 상태
	- TREX 라이브 전략은 단기 진입(ORB)·단기 청산(TSL)뿐. 장기 보유·비중 유지형 전략이 없다.
	- M3에서 중장기 trex-core 인스턴스 토대는 깔렸으나, 그 위에 얹을 실전 장기전략이 없다.
- 기대 상태
	- 운영자가 **종목별 목표비중을 config로 지정**하면, 데몬이 그 비중을 향해 **자동으로 부분 매수/부분 매도 리밸런싱**을 수행해 목표 배분을 유지한다.
	- 목표비중 값 자체는 사람이 고정(자동 변경 없음). 목표를 지키는 실행만 자동.
- 이해관계자
	- 사장님(전략 방향·목표비중·리스크 스탠스 결정)
	- 팀장(Discovery/거버넌스·검수)
	- 담당 개발 에이전트(REQ·구현), 운영팀(배포·관측)
- 제약사항
	- long-only 현금계좌 전제. 국내 단일종목 공매도 불가.
	- 전략 경로(strategies/)는 순수 결정론(IO/clock/random 없음), `trex_core`·라이브 인프라 무import(import sweep 강제).
	- trex-core는 명령형 qty+price intent만 수신하고 target 저장·reconcile을 하지 않는다 → 델타 계산은 전략 책임.

## 주요 사용자 시나리오

- 운영자가 목표 포트폴리오(예: 005930=30%, 000660=20%, 035720=15%, cash=35%)를 config에 넣고 데몬을 기동한다 → 데몬이 초기 목표 배분으로 매수해 포트폴리오를 확립한다.
- 특정 종목이 급등해 실제비중이 목표+밴드를 초과하면 → 데몬이 초과분을 자동 부분매도해 목표비중으로 되돌린다.
- 특정 종목이 하락해 실제비중이 목표-밴드 미만이면 → 데몬이 자동 추가매수(물타기)해 목표비중으로 되돌린다.

# 4. 이번에 정의할 변경

- **신규 전략**: constant-mix 목표비율 리밸런서(`tvr_v1`). per-종목 목표비중 + 암묵 cash 목표(100%−Σtarget_weight)를 유지.
- **전략 설정의 단일 진실원천 = portfolio-hub `strategy_sets` (사장님 확정 2026-07-02)**: 목표비중 등 per-종목 파라미터는 데몬 config 파일이 아니라 **portfolio-hub DB에 저장**하고, 유니버스(슬롯) 수신 시 함께 전달받는다.
	- **DB 확정 형태(기존 참조 모델 준수)**: `strategy_sets(name="tvr_v1", config='{"target_weight":0.30,"rebalance_band_pct":...,"min_trade_krw":...}' [JSON 문자열], nick="tvr_v1:<hash>")` ← (name,config) 유니크 — 비중 다른 종목은 전략셋 별도 발행. `portfolios.active_strategy_id`가 전략셋을 참조(per-포트폴리오=per-종목 바인딩), `managed_by_daemon_id`로 인스턴스 유니버스 스코핑.
	- **`name="tvr_v1"` 확정** — 데몬 strategy_id·repo명과 1:1. 기존 DB의 `target_value` 행(config `{}`, 포트폴리오 2개 참조)은 레거시 — 처리 방식은 REQ에서 결정(마이그레이션 or 방치).
- **슬롯 전략 필터(사장님 요구)**: tvr 데몬이 `trex.slots.req` 요청 시 **`strategy:"tvr_v1"` 필터**로 자기 전략 설정을 가진 슬롯만 수신한다. (daemon_id 유니버스 스코핑과 이중 방어 — 향후 한 인스턴스 다전략 공존 대비.)
- **wire 전달 3곳 확장**: ①portfolio-hub `/v1/portfolios` 응답에 `strategy_name`+`strategy_params`(config 해석) 노출 ②trex-core 슬롯 row에 strategy 필드 전달(SnapshotStore 경유) + slots.req `strategy` 필터 ③데몬이 row의 target_weight 사용.
- **프레임 확장(구조적)**: 현 template은 매수 전용(sell intent 미발신, 청산 core 위임). TVR은 자체 부분매도가 필요 → transport/intent_builder에 **sell-intent(부분매도) 경로 추가** + decision-cycle을 **리밸런싱형(매수/매도 분기)** 으로 확장.
- **장기 인스턴스 운용**: 하루 1회 리밸런스 체크, 밴드 게이팅으로 회전율·비용 억제.

## 영향 범위

- 코드 영향:
	- `trex_tvr/strategies/` — TvrStrategy 실제 리밸런싱 로직(현재 placeholder)
	- `trex_tvr/transport/` — sell-intent 포트/RPC 추가 + slots.req `strategy` 필터 사용
	- `trex_tvr/runtime/` — intent_builder sell payload, daemon decision-cycle 리밸런싱 확장
	- `strategies/base.py` — SellSignal/리밸런싱 평가 인터페이스
	- **portfolio-hub** — `/v1/portfolios` 응답에 `strategy_name`+`strategy_params`(active_strategy_id→strategy_sets 해석) 노출 (별도 REQ)
	- **trex-core** — 슬롯 row에 strategy 필드 전달 + `trex.slots.req` 요청 `strategy` 필터 (wire 계약 additive 확장, 별도 REQ)
	- `config/instance.example.toml` — 전략 파라미터는 portfolio-hub로 이동, 데몬엔 런타임 설정(주기·인스턴스키 등)만
- 문서 영향:
	- 본 Discovery → REQ(들) → batch. trex-core `interface-contract.md`(slots strategy 필터·row 필드, 자체 매도 발신 반영), portfolio-hub SRS.
- 운영 영향:
	- 신규 장기 trex-core 인스턴스(설정 TOML) 기동. **trex-core 코드 변경 있음**(wire additive — slots 필터/row 필드. 부분매도·증액·스냅샷 자체는 dcy-021/027 기구현). 배포 순서: portfolio-hub → trex-core → tvr 데몬.
	- daemon-scoped KIS 자격증명(dcy-010) 선행 필요. 관측성(status/health) 포함 출고.

# 5. 요구사항 목록

## 기능 요구사항
- FR-1: **per-종목 목표비중은 portfolio-hub `strategy_sets`에 저장**한다 — `name="tvr_v1"`, `config`(JSON 문자열)에 `target_weight`(+밴드·최소거래 임계). 포트폴리오가 `active_strategy_id`로 참조. cash 목표 = 100%−Σtarget_weight(암묵).
- FR-1b: portfolio-hub `/v1/portfolios` 응답이 포트폴리오별 `strategy_name`+`strategy_params`(config 해석)를 노출하고, trex-core가 이를 슬롯 row까지 전달한다.
- FR-2: 데몬은 매 리밸런스 주기 스냅샷(`trex.slots.req`, **`strategy:"tvr_v1"` 필터**)으로 **자기 전략 슬롯만** + 현재 보유수량(브로커 정본)+cash+target_weight를 읽어 종목별 현재비중을 계산한다(look-ahead=0).
- FR-3: 현재비중과 목표비중의 **드리프트가 밴드를 초과하는 종목만** 목표까지 조정한다(밴드 이내는 무거래 → 회전율·비용 억제).
- FR-4: 조정은 **부분매도(SellIntent.qty)와 증액/신규 매수(BuyIntent)** 로 수행하며, 한 리밸런스 내에서 **초과분 매도 → 현금 확보 → 부족분 매수** 순서로 처리한다.
- FR-5: 목표수량은 **whole-share 반올림**하고, **최소거래 임계** 미만의 미세조정은 생략한다(dust 거래 방지).
- FR-6: in-flight 충돌(대상 슬롯 PENDING_* → `already_active` 거부) 시 다음 주기 재시도로 재수렴한다.
- FR-7: 상태/헬스 관측(status/health snapshot: last_cycle_ts·last_status·의존성 상태 등)을 day-1부터 제공한다.

## 비기능 요구사항
- NFR-1: 회전율·비용 억제 — 밴드 게이팅으로 연 회전율이 과도하지 않게(구체 목표치는 설계 시뮬로 확정). 비용(수수료+슬리피지+KRX 매도세) 항상 반영.
- NFR-2: 시임 규칙 유지 — strategies/는 순수 결정론, `trex_core`·라이브 인프라 무import(import sweep 통과).
- NFR-3: 멱등성 — 재시도가 중복 주문을 만들지 않는다(intent_id + trex-core intent_log dedup).
- NFR-4: 안전 — 과매도 방지(브로커 잔고가 수량 상한 정본), 현금 부족 시 매수 축소.

## 범위 경계
- In Scope
	- per-종목 **고정** 목표비중 리밸런싱(초기 확립 + 유지)
	- 자체 부분매도 경로 신설(transport/engine 확장)
	- 밴드 게이팅 · 일 1회 체크 · 매도우선 순서 · whole-share/최소거래 임계 · in-flight 재시도
	- 관측성 day-1 · 장기 core 인스턴스 설정 · dcy-010 자격증명 연동
- Out of Scope
	- **목표비중을 기술지표·재무로 동적 자동조절** (→ v2, 별도 Discovery)
	- **동적 유니버스**(종목 자동 add/drop) — v1은 config 고정 목표셋
	- **손절/포지션·포트폴리오 리스크 가드** — v1은 순수 리밸런싱(하락장 물타기 감내, 리스크는 목표셋 종목 선정으로)
	- 세금최적화(tax-loss harvesting)·멀티계좌·고급 비용최적화

# 6. 리스크/가정 목록

## 리스크
- R-1 (High): 회전율 폭주 → 수수료·매도세로 수익 잠식. constant-mix의 대표 실패모드.
	- 완화 방안: 밴드 게이팅 필수 + 설계 단계 경량 시뮬로 밴드/주기/최소거래 임계를 회전율·비용 곡선 기준으로 확정.
- R-2 (Medium): 순수 리밸런싱은 **추세 하락장에서 계속 매수(물타기)** → 장기 약세장서 손실 누적.
	- 완화 방안: v1은 이를 **설계상 감내**(사장님 결정). 설계 시뮬에서 2022형 낙폭 행동을 명시 확인해 사장님이 인지된 상태로 배포. 필요 시 v2에서 레짐/가드 검토.
- R-3 (Medium): 프레임 매수전용 → sell-intent 경로 신설은 non-trivial(transport/engine/계약 변경).
	- 완화 방안: 별도 REQ/batch로 분리, wire 계약(자체 매도 발신)·시임 규칙 준수 검증.
- R-4 (Medium): cross-service wire 변경(portfolio-hub 응답 + trex-core slots 계약) — 기존 소비자(orb/tsl/web) 회귀 위험.
	- 완화 방안: **additive-only**(신규 필드·선택적 요청 파라미터만, 기존 필드/기본동작 불변). 서비스별 별도 REQ + 회귀 테스트. 배포 순서 portfolio-hub → trex-core → tvr.

## 가정
- A-1: trex-core는 부분매도(`SellIntent.qty`)·증액매수(HOLDING BuyIntent)·스냅샷 RPC·멱등 dedup을 이미 지원한다(dcy-021/027 조사 확정). ~~v1에 trex-core 코드 변경 불필요~~ → **(갱신 2026-07-02)** 전략 파라미터 wire 전달·slots `strategy` 필터를 위해 **trex-core additive 변경 필요**(hub_read는 이미 strategy_params 파싱 준비됨 — 끊긴 곳은 slot row 노출과 요청 필터).
- A-2: `max_active_slots ≥ 목표종목수`로 설정해 캡이 리밸런싱/초기확립을 막지 않는다.
- A-3: 데이터는 가격만(기존 일봉/현재가). v1에 신규 데이터 소스 불필요.
- A-4: 드리프트/부분체결 재수렴은 매 주기 스냅샷 재조회로 전략이 수행(core는 target 미저장·미reconcile — 전략 "설정"은 portfolio-hub가, 실행 "델타"는 데몬이 소유).
- A-5: 전략 설정 변경(비중 조정)은 **새 strategy_set 발행 + `active_strategy_id` 교체**로 이뤄진다((name,config) 유니크 — 셋 내용 불변 컨벤션 유지).

## 오픈 질문
- OQ-1: 밴드 정의(절대 %p vs 상대 %) 및 밴드 폭 값.
	- 상태: Deferred
	- 처리 방안: 설계 단계 경량 시뮬(6.5년 일봉)로 회전율·비용 곡선 산출 후 REQ에서 확정. 팀장 검수.
	- 종료 조건: 설계 시뮬 결과로 밴드 종류·값이 REQ에 반영되면 Answered.
- OQ-2: 최소거래 임계(dust 방지) 값 및 리밸런스 주기의 정확한 실행 시각(장마감후 일봉 확정 기준 vs 장중).
	- 상태: Deferred
	- 처리 방안: 설계 시뮬 + 인스턴스 운용 설계에서 확정. 장기 전략이므로 하루 1회 가정.
	- 종료 조건: REQ/인스턴스 설계에 값·시각 명시되면 Answered.
- OQ-3: 초기 포트폴리오 확립 시(전량 cash 시작) 신규 진입 순서·부분 확립 허용 여부.
	- 상태: Deferred
	- 처리 방안: REQ/설계에서 초기 확립 로직 정의(매수우선순위·캡 헤드룸). A-2 전제로 캡 충돌은 회피.
	- 종료 조건: REQ에 초기확립 규칙 명시되면 Answered.
- OQ-4: 기존 DB 레거시 `target_value` 전략셋(config `{}`, 포트폴리오 2개 참조) 처리 — `tvr_v1` 셋으로 마이그레이션? 방치? 해당 포트폴리오 2개가 tvr 관리 대상인지 확인.
	- 상태: Deferred
	- 처리 방안: portfolio-hub REQ에서 결정(사장님 확인: 그 포트폴리오 2개의 성격). name="tvr_v1" 확정에 따라 신규 발행이 기본.
	- 종료 조건: portfolio-hub REQ에 레거시 처리 방침 명시되면 Answered.

# 7. 초기 성공 기준

- S-1: config per-종목 목표비중 지정 → 데몬이 초기 목표 배분으로 포트폴리오를 확립한다.
- S-2: 가격 변동으로 드리프트가 밴드 초과 시, 다음 리밸런스에서 목표비중±허용오차로 자동 복원된다(look-ahead=0, 비용 반영).
- S-3: 밴드 게이팅으로 불필요한 미세조정 거래가 발생하지 않는다(밴드 이내 무거래).
- S-4: 자체 부분매도가 wire로 정상 발신·체결되고, 부분매도 후 슬롯이 HOLDING 유지(잔량·cost basis 보존)된다.

## 측정 방식 및 데이터 출처
- S-1 측정 방식: 백테스트/페이퍼에서 초기 배분 결과 비중 확인 / 데이터 출처: 백테스트 리포트·원장
- S-2 측정 방식: 드리프트 유발 시나리오 후 리밸런스 결과 비중 / 데이터 출처: 리밸런싱 로그·스냅샷
- S-3 측정 방식: 밴드 이내 구간 거래 건수=0 확인 / 데이터 출처: 주문 로그
- S-4 측정 방식: 부분매도 체결 후 슬롯 상태=HOLDING·avg_price 불변 / 데이터 출처: trex-core 슬롯 스냅샷(`sell_filled_partial`)

# 8. REQ로 넘기기 전 확인 체크

- [x] 범위(In Scope/Out of Scope) 확정
	- 기준: 사장님이 목표비중 모델(per-종목 비중)·리스크 스탠스(순수 리밸런싱)·v2 분리를 결정함(2026-07-02).
- [ ] 성공 지표의 측정 방식/데이터 출처 확정
	- 기준: S-1~S-4 측정치(허용오차·회전율 목표)를 설계 시뮬 후 수치화.
- [ ] High 영향 리스크 우선순위 및 해소 책임자 지정
	- 기준: R-1(회전율/비용) 해소 책임=담당 dev(설계 시뮬), 검수=팀장.
- [ ] 모든 오픈 질문(OQ) `Open` 상태 없음 확인
	- 기준: OQ-1~3 전부 Deferred 처리 완료(설계/REQ 인계). Open 0개.
- [ ] Freeze 확정 담당자(CONFIRMED_BY) 지정
	- 기준: 사장님 confirm-discovery.

# 9. 파일 처리 결과

- 처리 결과: 생성
- 생성 경로: docs/discovery/dcy-001_20260702_target-value-rebalancing.md
- 참조 문서:
	- GitHub 보드 #7 "★M4 메인: target-value 장기 리밸런싱 전략 신규"
	- trex-core dcy-021(포지션 사이징 increase/부분매도)·dcy-027(중장기 슬롯 운용)
	- portfolio-hub dcy-010(daemon-scoped 브로커 자격증명)

# 10. 사용자 결정 필요 항목 요약

## DECIDE
- (완료 2026-07-02) 목표비중 모델 = per-종목 목표비중. 리스크 = 순수 리밸런싱(손절 없음). v1은 고정목표, 동적조절은 v2.

## CONFIRM
- 사장님 confirm-discovery로 본 범위 Freeze 승인.

## DATA
- 없음(v1은 기존 가격데이터로 충분).

# 11. Discovery Freeze

## 섹션 참조
- 범위: [# 5. 요구사항 목록 > 범위 경계](#5-요구사항-목록)
- 리스크/오픈 질문: [# 6. 리스크/가정 목록](#6-리스크가정-목록)
- 성공 기준: [# 7. 초기 성공 기준](#7-초기-성공-기준)
- 인계 전 확인: [# 8. REQ로 넘기기 전 확인 체크](#8-req로-넘기기-전-확인-체크)
- 사용자 결정 필요: [# 10. 사용자 결정 필요 항목 요약](#10-사용자-결정-필요-항목-요약)

## Freeze 플래그
- Handoff Decision: 보류 (사장님 confirm-discovery 대기)
- Handoff Rationale: 범위 핵심 결정은 완료됐으나, 성공지표 수치화·OQ의 설계 시뮬 인계가 REQ 착수 전 정리 필요. confirm-discovery 승인 시 REQ Drafting 진행.
- Ready for REQ Drafting: false
- Confirmed By: TBD
- Confirmed At (KST): TBD
