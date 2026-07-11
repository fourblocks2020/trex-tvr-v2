# REL-001: TVR M4 constant-mix v1 첫 릴리즈

- Status: draft
- Profile: app-service
- Included Batch: BAT-001, BAT-002
- Draft Date: 2026-07-11
- Target Window: TBD — 사장님 release 결재 후 운영 윈도우 확정
- Release Gate: confirm-release — 팀장 검토·QC/e2e 증거 확인·사장님 결재 후 배포

## 릴리즈 개요

`trex-tvr-v1`의 첫 운영 릴리즈다. M4 메인 목표인 **target-value 장기 리밸런싱 전략 constant-mix v1**을 운영에 올린다.

v1의 핵심은 신규 데이터 소스 없이 현재 보유/현금/현재가와 포트폴리오별 `strategy_params`만으로 목표비중을 복원하는 price-only 리밸런싱이다.

- `inactive` 슬롯: 목표비중 대비 부족분을 매수 intent로 발행
- `holding` 슬롯: 목표비중 대비 초과분을 부분매도 sell intent로 발행
- 최소거래금액·whole-share·cash constraint·oversell clamp로 과도한 거래를 방지
- 08:00~20:00 세션 캘린더와 NXT 시장 게이트를 인지하되, NXT 실거래 사용 전에는 별도 스모크 게이트를 둔다

## Scope

### Included

| BAT ID | 요약 | Discovery / REQ | 상태 근거 |
| --- | --- | --- | --- |
| BAT-001 | TVR constant-mix 리밸런싱 코어, sell-intent 경로, 리밸런싱 decision-cycle, day-1 status/health | dcy-001 / REQ-001~004 | PR #1 merged, REQ Implemented, 88 passed, QC PASS-WITH-NOTES blocker 0 |
| BAT-002 | 세션 캘린더(08:00~20:00/NXT), 60s floor+no-sleep drain cadence, 다중 인스턴스 fan-in/라우팅 | dcy-002 / REQ-005~007 | PR #2 merged, REQ Implemented, 154 passed, QC PASS-WITH-NOTES blocker 0 |

### Cross-repo prerequisites included by dependency

이 릴리즈는 `trex-tvr-v1` 단독 코드만으로 완성되지 않는다. 아래 additive 변경이 운영 baseline에 포함되어야 한다.

| Repo | PR / REQ | 역할 | 릴리즈 전 상태 |
| --- | --- | --- | --- |
| `portfolio-hub` | PR #20 / REQ-055 | `/v1/portfolios` 응답에 `active_strategy_id`, `strategy_name`, `strategy_params`, `markets` 노출 | merged, local confirm-doc commit ahead 1 |
| `trex-core` | PR #37 / REQ-116 | `trex.slots.req` strategy 필터 + slot row `strategy_name/strategy_params/markets` 전달 | merged, origin/main 포함 |
| `trex-core` | PR #38 / REQ-117/118 | `OrderRequest.venue`, KIS broker `EXCG_ID_DVSN_CD`, current price market parameterization | merged, local confirm-doc commit ahead 1 |
| `kis-data-hub` | PR #21 / REQ-069~071 | quotes market `J/NX/UN` provider/API/test | merged, local confirm-doc commit ahead 1 |

### Excluded / M5 이월

- ORB↔kis-data-hub fetch 신뢰성 root-cause fix — BAT-022 관측성 적용 후 재확인
- TVR runtime architecture e2e/운영 재확인(dcy-002 후속) — M5
- TSL observability P1 — watchdog 필드·cycle history·notifier 연동 — M5
- TSL observability P2 — dashboard / metrics / web UI — M5
- TVR v2 동적 목표비율(기술지표·재무상태 기반) — 별도 Discovery
- NXT 연장세션 보호장치(OQ-3: spread guard, limit band, session-weighted band) — 초기 운영 관찰 후 change-req
- kdh 분봉 세션 창(08:00~20:00) 확장 — TVR v1 즉시 필요 범위 아님

## 현재 준비 상태

### 완료된 근거

- `trex-tvr-v1` dcy-001, dcy-002: confirmed
- `trex-tvr-v1` REQ-001~007: Implemented
- `trex-tvr-v1` PR #1, #2: merged
- 팀장 재실행: `python -m pytest tests/ -q` → `154 passed in 0.25s` (2026-07-11)
- 시임 규칙: `trex_tvr/` 전체 `trex_core` 무import, `strategies/` infra 무import 테스트 포함

### 아직 남은 release blocker

| ID | Blocker | 처리 필요 |
| --- | --- | --- |
| B-1 | 4개 repo의 `confirm-req-implemented` 문서 커밋이 local `main...origin/main [ahead 1]` | 사장님 지시 후 git-ops가 push |
| B-2 | cross-repo e2e wire 검증 미실행 | release 전 QC/e2e 체크리스트 수행 |
| B-3 | `trex-core` intent_id 포맷 전환에 따른 admitted/in-flight 상태 | 배포 전 `trex-core` 재시작 계획 필요 |
| B-4 | NXT 라이브 사용 전 `poll_fill` 구 TR의 NXT 체결 포함 여부 미확인 | KRX+NXT 소액 스모크로 확인 |
| B-5 | 운영 config 확정 필요 | TVR daemon config, instances, schedule/session, strategy_params, markets 확인 |

## 배포 의존성 / 순서

```text
[문서 baseline]
  portfolio-hub / trex-core / kis-data-hub / trex-tvr-v1 confirm-doc commits push
        ↓
[서비스 배포 순서]
  portfolio-hub PR #20 포함 버전 배포
        ↓
  kis-data-hub PR #21 포함 버전 배포
        ↓
  trex-core PR #37/#38 포함 버전 배포 + 재시작
        ↓
  trex-tvr-v1 PR #1/#2 포함 버전 배포
        ↓
[검증]
  cross-repo e2e smoke + 첫 거래일 운영 감시
```

### 순서 근거

1. `portfolio-hub`가 strategy/market meta를 먼저 실어줘야 한다.
2. `trex-core`가 해당 meta를 slots row로 흘려주고 strategy 필터를 처리해야 한다.
3. `kis-data-hub`가 market `J/NX/UN` 현재가 요청을 받아야 NXT/UN quote 경로가 열린다.
4. `trex-tvr-v1`는 위 계약을 소비해 strategy_params 기반 리밸런싱을 수행한다.

## 사전 조건 체크리스트

### 문서/코드 baseline

- [ ] `portfolio-hub` local ahead 1 confirm-doc commit push 완료
- [ ] `trex-core` local ahead 1 confirm-doc commit push 완료
- [ ] `kis-data-hub` local ahead 1 confirm-doc commit push 완료
- [ ] `trex-tvr-v1` local ahead 1 confirm-doc commit push 완료
- [ ] `trex-tvr-v1` release draft commit push 완료

### 서비스 배포 전

- [ ] `portfolio-hub` 운영 버전에 PR #20 포함 확인
- [ ] `kis-data-hub` 운영 버전에 PR #21 포함 확인
- [ ] `trex-core` 운영 버전에 PR #37/#38 포함 확인
- [ ] `trex-core` 재시작 계획 확정 — admitted/in-flight 초기화 목적
- [ ] TVR 운영 config 작성/검토
  - [ ] `strategy_id = "tvr_v1"`
  - [ ] `poll_interval = 60`
  - [ ] `session_start = "08:00"`, `session_end = "20:00"`
  - [ ] `instances = [...]` 운영 core 인스턴스 목록
  - [ ] status path 설정
- [ ] `portfolio-hub` 운영 데이터 확인
  - [ ] 대상 portfolio의 `active_strategy_id`가 `tvr_v1` strategy set에 연결됨
  - [ ] `strategy_params.target_weight`, `rebalance_band_pct`, `min_trade_krw` 존재
  - [ ] `assets.markets` 설정 확인 (`[]`은 보수적으로 KRX only)
- [ ] RabbitMQ / trex-core slots/intent 큐 가동 확인
- [ ] kis-data-hub `/v1` 또는 `/v2/quotes/latest` 현재가 응답 정상 확인

### release 전 e2e smoke

- [ ] `portfolio-hub GET /v1/portfolios` 응답에 `strategy_name`, `strategy_params`, `markets`가 포함됨
- [ ] `trex-core trex.slots.req`에 `strategy="tvr_v1"` 요청 시 TVR 슬롯만 반환됨
- [ ] slot row에 `strategy_params`와 `markets`가 포함됨
- [ ] `trex-tvr-v1` decision-cycle dry/smoke에서 `missing_strategy_params` 없이 평가됨
- [ ] 매수 후보 슬롯에서 BuyIntent payload 생성 확인
- [ ] 보유 초과 슬롯에서 SellIntent payload 생성 확인
- [ ] 동일 slot_id가 다른 instance에 있어도 intent_id가 instance_key로 구분됨
- [ ] NXT 전용 시간대에서 `markets`에 `NXT`가 없으면 intent 발신 억제됨
- [ ] NXT 라이브 사용 전 KRX+NXT 각 1건 소액 주문/취소/체결조회 smoke 계획 승인

## Rollout Plan

### Step 0 — release 승인 전 준비

```bash
# 각 repo에서 문서 baseline push 전 상태 확인
git status --short --branch
git log --oneline -3
```

- main push는 사장님 지시 후 git-ops가 수행한다.
- release confirm 전에는 실제 배포/재시작을 수행하지 않는다.

### Step 1 — cross-repo baseline 배포

운영 배포 디렉토리에서 각 repo를 origin/main 최신으로 맞춘다.

```bash
# portfolio-hub / kis-data-hub / trex-core / trex-tvr-v1 각각
git fetch origin
git checkout main
git pull --ff-only origin main
git log --oneline -3
```

### Step 2 — 의존성 설치/서비스 재시작

각 repo 운영 방식에 맞춰 의존성 설치 후 재시작한다.

```bash
# trex-tvr-v1 예시
cd <trex-tvr-v1 배포 디렉토리>
.venv/bin/pip install -e .
.venv/bin/tvr --help || .venv/bin/trex-tvr-v1 --help
```

> 실제 CLI 이름은 운영 설치 결과로 확인한다. repo README/pyproject 기준과 운영 alias가 다르면 배포 기록에 남긴다.

### Step 3 — trex-core 재시작

- 목적: TVR intent_id 포맷 전환에 따른 admitted/in-flight 상태 초기화
- 기대: 기존 slot 상태는 DB/portfolio 상태에 남아 있으므로 already_active/holding guard로 재수렴
- 절차: 운영 중인 trex-core 장기 인스턴스 graceful restart

### Step 4 — TVR 데몬 기동

```bash
# 운영 config 경로 예시
.venv/bin/tvr run --config <운영-config.toml>
# 또는 설치된 실제 CLI명 사용
```

기동 로그 확인:

- `strategy_id=tvr_v1`
- `poll_interval=60`
- session `08:00~20:00`
- instances 목록
- status snapshot write 성공
- slots.req strategy filter 사용

## 배포 후 Verification Checklist

### 기본 health/status

- [ ] TVR 프로세스 가동 확인
- [ ] `tvr status` 또는 status JSON 확인
- [ ] `last_cycle_ts` 갱신
- [ ] `last_status`가 `ok`, `session_hold`, `degraded_hold` 중 기대 상태
- [ ] deps.rabbitmq / deps.kis_data_hub 상태 확인

### decision-cycle smoke

- [ ] cycle log에 `slots`, `evaluated`, `signals`, `admitted`, `diagnostics_total` 유사 지표 확인
- [ ] 정상 슬롯에 `missing_strategy_params` 반복 발생하지 않음
- [ ] `stale_quote` / `quote_unavailable` 발생 시 intent 미발신
- [ ] `already_active` 응답이 예외로 escalates 되지 않고 다음 cycle 재수렴

### order path smoke

- [ ] KRX 경로 소액 주문/취소/체결조회 smoke PASS
- [ ] NXT 경로 사용 전 소액 주문/취소/체결조회 smoke PASS 또는 NXT 사용 비활성화 유지
- [ ] `poll_fill`이 NXT 체결을 포함하지 않는 것으로 확인되면 NXT 실거래 비활성화 후 후속 batch 생성

### 첫 거래일 운영 감시

- [ ] 첫 cycle 시작/종료 로그 확인
- [ ] TVR 슬롯 수와 대상 portfolio 수 일치 확인
- [ ] 과도한 반복 주문 없음
- [ ] buy/sell intent 수량이 목표비중·보유수량·현금 제약과 일치
- [ ] portfolio-hub ledger/state에서 체결 결과 확인
- [ ] 문제 발생 시 TVR 데몬 중지 → trex-core safety/기존 포지션 보존 상태 확인

## Rollback Plan

### 롤백 조건

다음 중 하나 발생 시 즉시 중단/롤백한다.

- TVR 데몬 기동 실패 또는 status snapshot 미갱신
- `strategy_params` 누락으로 전체 슬롯 평가 불가
- 잘못된 인스턴스 큐로 intent routing
- 과매수/과매도 의심 수량 발생
- NXT 주문/취소/체결조회 smoke 실패 후 NXT 경로가 계속 활성화됨
- trex-core 주문 경로 회귀 또는 orb/tsl 영향 관측

### 롤백 절차

1. `trex-tvr-v1` 데몬 중지
2. 신규 TVR strategy set / portfolio binding 비활성화 또는 `active_strategy_id` 해제
3. 필요 시 `trex-core` 재시작으로 in-flight/admitted 상태 재정리
4. 코드 롤백이 필요하면 직전 origin/main 안정 커밋으로 각 서비스 복귀
5. portfolio-hub ledger/state에서 미체결/체결/잔고 확인
6. 원인 분석 후 M5 후속 Discovery 또는 hotfix로 환류

## Known Issues / Non-blocking Notes

- `trex-tvr-v1` BAT-001 REQ-004 AC-4/5 transport error/reconnect deps 갱신은 코드 구현됐으나 실제 transport 통합 검증은 제한적이다. release smoke에서 status/deps 갱신을 확인한다.
- `portfolio-hub` REQ-055 dangling FK LEFT JOIN NULL 실DB 확인은 배포 후 통합검증 권장사항이며 release blocker는 아니다.
- `kis-data-hub` REQ-069~071의 v2 stale=True + Redis hit 경로 market 미적용 한계는 후속 권고사항이다. TVR release에서는 stale quote를 보수적으로 다루고, NXT 실거래 전 smoke로 보완한다.
- NXT extended-session spread/limit 보호장치는 M5 후속이다. M4 첫 릴리즈에서 NXT를 실제로 켤 경우 소액 smoke와 운영자 감시를 필수로 한다.

## Independent Release Draft Review

### REV-20260711-01 — qc-style read-only review

- Date: 2026-07-11
- Reviewer: independent delegated review
- Verdict: **FAIL — 현 상태로 confirm-release 진행 금지**

#### 확인된 정상 근거

- REL-001 draft 및 release index 등록 확인.
- BAT-001/BAT-002 release-candidate 정렬 확인.
- REQ-001~007 Implemented 확인.
- TVR 테스트 재실행: `154 passed in 0.53s`.
- GitHub Project M4 메인 카드 `Release-Pending` 확인.

#### confirm-release 전 필수 blocker

1. **local ahead commit 미푸시**: `portfolio-hub`, `trex-core`, `kis-data-hub`, `trex-tvr-v1` 문서/릴리즈 커밋 push 필요.
2. **cross-repo e2e wire 검증 미실행**: 본 문서 release 전 e2e smoke 체크리스트 PASS 필요.
3. **운영 config / strategy_params / markets 미확정**: 실제 운영 대상 portfolio/strategy_set/config 확인 필요.
4. **trex-core 재시작 계획 필요**: intent_id 포맷 전환에 따른 admitted/in-flight 초기화 계획 확정 필요.
5. **NXT 정책 결정 필요**: NXT 비활성 시작 또는 KRX+NXT 소액 주문/취소/체결조회 smoke PASS 후 활성화 중 하나로 확정 필요.

#### WARN / 문서 불일치

- BAT-001/BAT-002 `verification.md` 일부 문구가 stale 상태(`draft`, `Ready for Release: false`, `READY FOR QC-REVIEWER`)를 유지한다.
- 최신 source는 batch index confirm 기록, SRS index, 본 release draft다. confirm-release 메모에서 stale verification 문구를 명시적으로 무효화하거나 별도 문서 정리를 수행한다.
- cross-repo repos의 untracked `.claude/settings.local.json`는 stage 금지.

#### 권고 게이트 순서

1. git-ops push 승인/실행.
2. 운영 baseline sync 확인.
3. 운영 config/strategy_set/markets 확정.
4. cross-repo e2e smoke 수행.
5. trex-core restart 계획 확정.
6. NXT 정책 확정.
7. 위 evidence를 Release Execution Evidence 또는 confirm-release 메모에 기록 후 confirm-release 진행.

## Release Execution Evidence

*(confirm-release 및 실제 배포 후 채움)*

- Release Status Transition Target: released 또는 feedback-captured

### Deployment Facts

- Executed At:
  - TBD
- Completed At:
  - TBD
- Environment:
  - production / release host
- Operator:
  - TBD
- Revision or Artifact:
  - portfolio-hub: TBD
  - kis-data-hub: TBD
  - trex-core: TBD
  - trex-tvr-v1: TBD
- Commands or Procedure:
  - 위 Rollout Plan 참조

### Profile-Specific Checks

- app-service health/status:
  - TBD
- Smoke / manual verification:
  - TBD
- First trading day verification:
  - TBD

### Rollback Outcome

- Rollback Required:
  - TBD
- Rollback Trigger:
  - TBD
- Rollback Action:
  - TBD

## Follow-up / Feedback Capture

- M5: TVR runtime architecture e2e/운영 재확인 카드로 운영 결과 환류
- M5: TSL observability P1/P2 별도 Discovery
- M5: ORB↔kdh fetch 신뢰성 재발 로그 BAT-022 관측성 기반 재판정
