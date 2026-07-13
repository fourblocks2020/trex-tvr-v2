# REL-001: TVR M4 constant-mix v1 첫 릴리즈

- Status: released (confirm-release, 2026-07-12; deployed idle, strategy cutover pending)
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
- 08:00~20:00 세션 캘린더와 NXT 시장 게이트를 인지하지만, **M4 첫 릴리즈 운영은 KRX 전용으로 시작한다**. NXT 실거래 활성화는 M5 또는 별도 KRX+NXT smoke PASS 후 진행한다.

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
| B-1 | 4개 repo의 `confirm-req-implemented` 문서 커밋이 local `main...origin/main [ahead 1]` | **해소됨 — 2026-07-11 git-ops push 완료** |
| B-2 | cross-repo e2e wire 검증 미실행 | **해소됨 — 2026-07-12 배포 후 portfolio-hub/trex-core/kdh 계약 확인. 단 TVR 대상 슬롯은 아직 0개라 first active cycle은 feedback 후속** |
| B-3 | `trex-core` intent_id 포맷 전환에 따른 admitted/in-flight 상태 | **계획 수립됨 — 아래 KRX-only rollout gate의 trex-core graceful restart 순서 적용. 실행은 confirm-release/배포 시점** |
| B-4 | NXT 라이브 사용 전 `poll_fill` 구 TR의 NXT 체결 포함 여부 미확인 | **해소/범위제외 — M4 첫 릴리즈는 KRX 전용. NXT 활성화는 M5 또는 별도 smoke PASS 후 진행** |
| B-5 | 운영 config 확정 필요 | **해소됨 — `/root/release/trex-tvr-v1/config/tvr-m4-krx.toml`, KRX-only, `instances=["genichin-longterm"]`. 단 portfolio TVR cutover는 후속 운영 조치** |

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
3. `kis-data-hub`가 market `J/NX/UN` 현재가 요청을 받을 수 있어야 한다. 단, M4 첫 운영은 KRX 전용이므로 기본 `J` 경로를 사용하고 `NX/UN` 실거래 활성화는 후속으로 둔다.
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
- [ ] M4 첫 운영은 KRX 전용이므로 NXT 전용 시간대 intent 발신이 억제됨
- [ ] NXT 라이브는 비활성 상태로 시작함을 운영 config/절차에 명시

## KRX-only pre-confirm gates (M4)

M4 첫 릴리즈는 KRX 전용이다. 따라서 confirm-release 전에 NXT smoke를 요구하지 않고, **NXT 비활성/억제 상태가 보장되는지**를 확인한다.

### 운영 config 기준

운영 TOML은 아래 조건을 만족해야 한다. 실제 secret/URL은 `.env` 또는 환경변수로 주입하고 릴리즈 문서에 원문 값을 남기지 않는다.

```toml
instance_key = "<tvr-instance-key>"
instances = ["<trex-core-instance-key>"]
poll_interval = 60
rpc_timeout_seconds = 5
worker_count = 20
session_start = "08:00"
session_end = "20:00"

[[schedule]]
start = "08:00"
end = "20:00"

[strategy.tvr_v1]
# target_weight / rebalance_band_pct / min_trade_krw는 portfolio-hub strategy_sets에서 전달

[execution]
timeout_sec = 30
slippage_pct = 0.5
```

환경변수/`.env` 필수 항목:

```text
TREX_RABBITMQ_URL=[REDACTED]
KIS_DATA_HUB_BASE_URL=<kdh internal URL>
KIS_DATA_HUB_API_KEY=[REDACTED or empty if unused]
KIS_DATA_HUB_ALLOW_STALE=false
TREX_REDIS_URL=[REDACTED]
TREX_TVR_STATUS_PATH=<운영 status JSON 경로, 선택>
```

팀장 로컬 config parser smoke:

```text
strategy_id= tvr_v1
instance_key= tvr-m4-krx
instances= ['longterm']
poll_interval= 60
schedule= [('08:00', '20:00')]
allow_stale= False
execution= ExecutionConfig(timeout_sec=30, slippage_pct=0.5)
```

### KRX-only data policy

- portfolio-hub 대상 assets의 `markets`는 `[]` 또는 `['KRX']`로 시작한다.
- `markets=[]`는 TVR에서 보수적으로 KRX only로 해석된다.
- `markets`에 `NXT`가 포함된 슬롯은 M4 첫 릴리즈 대상에서 제외한다.
- NXT 전용 구간(PRE_MARKET/AFTER_SINGLE/AFTER_MARKET)에서는 KRX-only 슬롯이 `market_not_eligible` 진단으로 intent 발신되지 않아야 한다.

### trex-core restart plan

`trex-tvr-v1` BAT-002에서 intent_id 포맷이 instance-aware로 바뀌었다.

```text
buy:  {strategy}:{instance}:{date}:{slot}
sell: sell:{strategy}:{instance}:{date}:{slot}
```

배포 절차:

1. TVR 데몬이 아직 기동 중이면 중지한다.
2. 기존 trex-core 장기 인스턴스를 graceful stop한다.
3. 프로세스 종료와 RabbitMQ consumer 정리를 확인한다.
4. trex-core를 origin/main 최신 코드로 재기동한다.
5. slots/intent 큐가 정상 bind되는지 확인한다.
6. 그 다음 TVR 데몬을 기동한다.

의도:

- trex-core in-memory admitted/in-flight intent 상태를 초기화한다.
- 영구 slot/portfolio 상태는 DB에 유지되므로, 재기동 후 이미 보유 중인 슬롯은 already_active/holding 경로로 재수렴한다.
- 재시작 자체는 confirm-release/배포 승인 이후 실행한다. 현재 단계에서는 계획 확정까지만 수행한다.

### Release 전 e2e smoke 최소 기준

실제 운영/스테이징 환경에서 다음을 확인해야 confirm-release로 넘어갈 수 있다.

1. `portfolio-hub` `/v1/portfolios` 응답에 대상 portfolio의 `strategy_name=tvr_v1`, `strategy_params`, `markets`가 존재한다.
2. `trex-core` `trex.slots.req`에 `strategy='tvr_v1'` 요청 시 TVR 대상 슬롯만 반환된다.
3. 반환 slot row에 `strategy_params.target_weight`, `rebalance_band_pct`, `min_trade_krw`가 포함된다.
4. `markets`가 `[]` 또는 `['KRX']`로 확인된다.
5. TVR dry/smoke cycle에서 `missing_strategy_params` 반복 없이 평가된다.
6. KRX MAIN 구간 또는 clock-injected smoke에서 BuyIntent/SellIntent payload가 정상 생성된다.
7. intent_id에 instance_key가 포함된다.
8. NXT 전용 구간에서는 KRX-only 슬롯 intent가 억제된다.

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
- [ ] NXT 사용 비활성화 유지 확인 — KRX 전용 운영
- [ ] NXT 활성화 필요 시 M5/후속 batch에서 소액 주문/취소/체결조회 smoke 및 `poll_fill` 검증 수행

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
- M4 KRX 전용 정책에도 NXT 경로가 활성화됨
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
- `kis-data-hub` REQ-069~071의 v2 stale=True + Redis hit 경로 market 미적용 한계는 후속 권고사항이다. M4 첫 TVR release는 KRX 전용으로 시작해 NXT/UN 경로 리스크를 운영 범위에서 제외한다.
- NXT extended-session spread/limit 보호장치는 M5 후속이다. M4 첫 릴리즈에서는 NXT를 켜지 않는다.

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

1. **local ahead commit 미푸시**: `portfolio-hub`, `trex-core`, `kis-data-hub`, `trex-tvr-v1` 문서/릴리즈 커밋 push 필요. **해소됨 — 2026-07-11 git-ops push 완료.**
2. **cross-repo e2e wire 검증 미실행**: 본 문서 release 전 e2e smoke 체크리스트 PASS 필요.
3. **운영 config / strategy_params / markets 미확정**: 실제 운영 대상 portfolio/strategy_set/config 확인 필요.
4. **trex-core 재시작 계획 필요**: **계획 수립됨 — KRX-only pre-confirm gates의 restart plan 적용. 실제 재시작은 confirm-release/배포 승인 후 실행.**
5. **NXT 정책 결정 필요**: **해소됨 — M4 첫 릴리즈는 KRX 전용, NXT 비활성 시작.**

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
6. NXT 정책 확정 — 완료: M4 첫 릴리즈 KRX 전용.
7. 위 evidence를 Release Execution Evidence 또는 confirm-release 메모에 기록 후 confirm-release 진행.

## Ops Preflight Evidence

### OPS-20260711-01 — TVR 제외 선행 서비스 preflight 회신

- Source: `deploy-20260711-235309-adhraof5m3dx3quqinihanqw6w.md`
- Verdict: **FAIL / preflight 미종결**

#### 확인된 정상 근거

- `portfolio-hub`: 운영 DB 기준 portfolio 146/146 모두 `markets=["KRX"]`, dangling join 없음.
- `kis-data-hub`: health / quote / stale 경로는 정상 신호. KRX quote read 특이사항 없음.

#### blocker

1. **운영 checkout HEAD가 기대 baseline보다 오래됨**
   - `portfolio-hub`: expected `6b695b8`, current `9dcd00f` — current는 expected의 ancestor.
   - `trex-core`: expected `7845fbe`, current `314047b` — current는 expected의 ancestor.
   - `kis-data-hub`: expected `5078b34`, current `6935920` — current는 expected의 ancestor.
   - 결론: 세 서비스 모두 TVR M4 prerequisite commit이 아직 운영 checkout에 반영되지 않았다.
2. **운영 API/RPC 계약 증거 부족**
   - `portfolio-hub` 운영 API/OpenAPI에서 `strategy_name` / `strategy_params` / `markets` 노출 미확인.
   - `trex-core` 실제 `slots.req` row에 `strategy_name` / `strategy_params` / `markets` 필드 없음.
   - `strategy=tvr_v1` payload가 런타임 계약상 어떻게 반영되는지 증빙 부족.
3. **TVR 설정값 사전 미구성은 blocker가 아니지만, 배포 시점 반영 절차/기준 commit 확정 필요**
   - TVR 값은 배포 시점 최종 결정 가능.
   - 단, 선행 서비스가 기대 baseline에 올라간 후 동일 preflight를 재수행해야 한다.

#### 팀장 판정

- `trex-tvr-v1` 배포 가이드 작성은 진행 가능.
- 그러나 confirm-release 또는 운영 배포 GO는 불가.
- 다음 순서: TVR 제외 선행 서비스 3개를 기대 baseline 이상으로 배포/동기화 → preflight 재실행 → TVR 배포 가이드 기준으로 `trex-tvr-v1` 배포 준비.

## Release Execution Evidence

### OPS-20260712-01 — M4 TVR KRX-only 배포 실행 결과

- Source: 운영팀 실행 결과, 작성시각 2026-07-12 00:37:59 KST
- 기준 문서: `/root/.hermes/cache/documents/doc_35a8bdaf779d_m4-tvr-krx-only-deployment-request-x8byf4njzbnw3d6qncbgasitoh.md`
- 팀장 판정: **confirm-release 가능 / release status = released**
- 단서: 코드·프로세스 배포는 성공했으나, 운영 데이터는 아직 `tvr_v1`로 cutover되지 않아 TVR은 정상 idle 상태. first active cycle은 release feedback 후속으로 추적한다.

#### Deployment Facts

- Executed / Completed: 2026-07-12 00:37:59 KST 보고 기준 완료
- Environment: production / release host
- Deployed Revisions:
  - `portfolio-hub`: `6b695b8`
  - `kis-data-hub`: `5078b34`
  - `trex-core`: `7845fbe`
  - `trex-tvr-v1`: `598ced4`
- Commands / Procedure:
  - `bash /root/release/scripts/deploy-portfolio-hub.sh`
  - `bash /root/release/scripts/deploy-kis-data-hub.sh`
  - `bash /root/release/scripts/deploy-trex-core.sh`
  - `trex-tvr-v1` 신규 clone, `.venv`, `pip install -e .`, config/env 작성, supervisor 등록/기동

#### Service Health / Process Evidence

- `./scripts/status-all.sh --json` → `overall_ok=true`
- `trex-tvr-v1` 포함 전체 10개 프로세스 `RUNNING`
- `portfolio-hub` health: `http://127.0.0.1:8002/health` → `200`
- `kis-data-hub` health: `http://127.0.0.1:8003/health` → `200`

#### Cross-repo Contract Evidence

- `portfolio-hub` OpenAPI `PortfolioItem` field 확인:
  - `active_strategy_id`
  - `strategy_name`
  - `strategy_params`
  - `markets`
- 인증된 `/v1/portfolios` 실응답에서도 위 필드 노출 확인.
- 운영 데이터 요약:
  - total portfolios: `146`
  - `strategy_name` 분포: `target_value=146`
  - `tvr_v1` 또는 TVR 파라미터 대상: `0`
- `trex-core` `slots.req` row field 확인:
  - `strategy_name`
  - `strategy_params`
  - `markets`
- `genichin-longterm` slot counts:
  - `filter=inactive,holding` → `slot_count=1`
  - `strategy=target_value` → `slot_count=1`
  - `strategy=tvr_v1` → `slot_count=0`
- 판정: wire 계약은 열렸고 동작 증거가 있다. 단 현재 운영 데이터 기준 TVR 대상 슬롯은 없다.
- `kis-data-hub`: 인증된 `POST /v1/quotes/latest` 성공, sample `005930`, `stale=false`, live quote 정상.

#### trex-tvr-v1 Runtime Evidence

- supervisor status: `RUNNING`
- status snapshot:
  - `deps.rabbitmq=ok`
  - `deps.kis_data_hub=ok`
  - `deps.redis=ok`
  - `last_status=skipped`
  - `idle_reason=out_of_schedule`
- foreground smoke: RabbitMQ / kis-data-hub / Redis startup probe 성공.
- config path: `/root/release/trex-tvr-v1/config/tvr-m4-krx.toml`
- status path: `/root/release/logs/trex-tvr-v1-status.json`
- config summary:
  - `instance_key="tvr-m4-krx"`
  - `instances=["genichin-longterm"]`
  - `poll_interval=60`
  - `session_start="08:00"`, `session_end="20:00"`
  - `execution.timeout_sec=30`
  - `execution.slippage_pct=0.5`

#### Release Result

- Deployment result: **SUCCESS**
- Strategy activation result: **not cutover yet / idle 정상**
- Rollback Required: no
- Release Status Transition: `released`

#### Remaining Operational Follow-up

1. `portfolio-hub` 대상 portfolio를 `tvr_v1` strategy set에 연결.
2. 각 대상에 `strategy_params.target_weight`, `rebalance_band_pct`, `min_trade_krw` 반영.
3. 필요 시 TVR fan-in instance를 `trex-admin-daily-trading` 또는 다중 instance로 확장.
4. 장중/세션 내 `trex-tvr-v1 status`와 log 기준 first active cycle 재검증.

## Follow-up / Feedback Capture

- M5: TVR runtime architecture e2e/운영 재확인 카드로 운영 결과 환류
- Release feedback / architecture debt: `portfolio-hub.strategy_sets(name, config TEXT)`는 M4 KRX-only의 단순 TVR 설정에는 동작하지만, 복잡한 전략 설정·포트폴리오별 override가 늘어나면 `strategy_sets` row 폭증, 의미 추적 어려움, 감사/승인/rollback 취약성이 커진다.
  - 사장님 피드백: 이런 설계인 줄 알았다면 강하게 반대했을 사안. M5에 억지로 넣기에는 어렵고, 심각한 별도 설계 검토가 필요.
  - 단기 운영 원칙: `strategy_sets.config` 직접 UPDATE 금지, portfolio 전략 변경은 API/CLI의 get-or-create 경로만 사용, TVR cutover 대상은 최소화, 사용한 config/포트폴리오 매핑은 운영 evidence로 남김.
  - 후속 후보: M5 강제 편입이 아니라 post-M5 또는 별도 Discovery 후보로 `strategy preset / portfolio assignment / override / versioning / audit` 모델을 검토.
- M5: TSL observability P1/P2 별도 Discovery
- M5: ORB↔kdh fetch 신뢰성 재발 로그 BAT-022 관측성 기반 재판정
