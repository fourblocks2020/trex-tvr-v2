# OPS-20260720-REL-002 — TREX TVR quote-only market-data 배포 요청

- 요청 상태: **배포 실행 요청 — 운영팀 GO/실행 결과 대기**
- 대상 환경: production
- 요청 서비스: `trex-tvr-v1` only
- 릴리즈: `REL-002` / `BAT-003` / `REQ-003@2`
- 요청 기준 시각: 2026-07-20 14:39 KST
- 소스 기준: `origin/main` = `32c5b1e48b50d1e8556b2d3b99b77760aacbbf5a`
- 배포 payload: `9398b2d` — `fix(tvr): skip candles for quote-only market data`
- 요청자: TREX 개발팀

## 1. 변경 목적과 범위

`KisDataHubClient.fetch_market_data()`에서 `candle_count <= 0`인 quote-only 요청이 minute-candle endpoint를 호출해 HTTP 400이 발생할 수 있던 문제를 수정합니다.

- non-positive count: 현재가만 조회하고 `candles=[]` 반환
- positive count: 기존 minute-candle 조회 및 KST 당일 필터링 동작 유지
- daemon quote-only BuySignal: minute-candle 요청 없이 buy intent 1건을 제출하는 회귀 경로 검증

### 비범위

- `kis-data-hub`, `trex-core`, `portfolio-hub` 코드·HTTP/AMQP 계약 변경 없음
- DB migration, 설정 스키마 변경, 자격증명 변경 없음
- 전략 산식·주문 수량·세션/polling 정책 변경 없음
- 실제 주문을 위한 신규 테스트 주문은 본 요청 범위가 아님

## 2. 사전 검증 근거

| 항목 | 결과 |
|---|---|
| Targeted regression | `19 passed` |
| Full test suite | `158 passed` |
| Source hygiene | `git diff --check` 통과, targeted Ruff 통과 |
| Independent QC | `PASS-WITH-NOTES`, 제품 코드 blocker 없음 |
| Batch verification | BAT-003 confirmed |
| main / origin/main 일치 | `32c5b1e48b50d1e8556b2d3b99b77760aacbbf5a` |

## 3. 운영팀 실행 요청

### 사전 확인 (모두 충족 시에만 진행)

1. 운영 checkout의 `origin/main`이 `32c5b1e48b50d1e8556b2d3b99b77760aacbbf5a`를 해석하는지 확인한다.
2. 현재 운영 TVR revision, 서비스 상태, 마지막 정상 cycle 시각을 배포 기록에 남긴다.
3. TVR의 미처리 주문/intent 및 시장 운영 상태를 확인한다. 활성 주문 또는 판단 중인 cycle이 있으면 해당 cycle 정리 후 진행한다.
4. 배포 전 `tvr status`(또는 운영 status JSON)와 관련 TVR 로그의 기준 스냅샷을 저장한다.
5. `kis-data-hub` health 및 TVR의 RabbitMQ/KIS 의존성 상태가 정상인지 확인한다.

### 배포 절차

1. 운영 표준 runbook으로 `trex-tvr-v1` checkout을 기준 SHA `32c5b1e48b50d1e8556b2d3b99b77760aacbbf5a`로 갱신한다.
2. 기존 운영 방식으로 의존성을 동기화하고, 설정·환경변수는 변경하지 않는다.
3. 운영 표준 절차에 따라 **`trex-tvr-v1`만** graceful restart 한다.
4. restart 직후 프로세스 PID/기동 시각/배포 revision을 배포 기록에 남긴다.

## 4. 배포 후 확인 기준

### 즉시 확인

- [ ] TVR 프로세스가 RUNNING 상태
- [ ] `tvr status` 또는 상태 JSON이 정상 갱신
- [ ] `last_cycle_ts`가 기대 주기에 따라 갱신
- [ ] RabbitMQ 및 kis-data-hub 의존성 상태 정상
- [ ] daemon 예외/재시작 루프 없음

### quote-only 변경 확인

- [ ] `candle_count=0` 또는 음수의 quote-only 경로에서 minute-candle HTTP 요청이 발생하지 않음
- [ ] 기존 `count=0` minute-candle HTTP 400 로그가 재발하지 않음
- [ ] 양수 candle count 전략 cycle의 기존 minute-candle 조회가 정상
- [ ] 예상하지 않은 buy/sell intent 증가 또는 중복 주문 없음

### 관측 기간

- 최소 첫 정상 decision cycle 및 첫 quote-only 경로 관측까지 감시한다.
- 문제·경보·주문 이상이 없으면 운영팀은 배포 완료 시각, deployed SHA, health/status 결과, 로그 근거 위치를 회신한다.

## 5. 중단·롤백 기준

다음 중 하나라도 발생하면 신규 cycle 진행을 중단하고 롤백한다.

- TVR 기동 실패, status snapshot 미갱신, 또는 반복 재시작
- quote-only 경로에서 minute-candle 요청 또는 HTTP 400 재발
- 의존성 health 실패
- 예상하지 않은 intent/주문 증가 또는 주문 상태 이상

### 롤백 절차

1. TVR 신규 cycle을 중지하고 현재 상태·로그·배포 revision을 보존한다.
2. 사전 확인에서 기록한 **직전 운영 기준 revision**으로 `trex-tvr-v1`을 복귀한다.
3. 운영 표준 절차로 TVR를 재기동한다.
4. `tvr status`, 의존성 health, 미처리 intent/주문 상태를 재확인한다.
5. 증적과 함께 개발팀에 즉시 회신한다. 코드 원인이 확인되면 `9398b2d`를 대상으로 hotfix/revert 판단을 별도 진행한다.

## 6. 운영팀 회신 양식

```text
OPS-20260720-REL-002 결과
- 실행 시각(KST):
- 운영 환경/호스트 식별자:
- deployed SHA:
- 이전 revision:
- TVR process/status 결과:
- kis-data-hub/RabbitMQ 상태:
- 첫 정상 cycle 시각:
- quote-only 관측 결과 (minute-candle 요청/HTTP 400 여부):
- 주문/intent 이상 여부:
- 결과: PASS / ROLLBACK / BLOCKED
- 로그·대시보드·증적 위치:
- 특이사항 및 후속 조치:
```

## 7. 승인 경계

이 문서는 운영팀에 **실행을 요청하는 배포 요청서**입니다. 실제 배포, restart, 주문 경로 관측, 롤백은 운영팀의 실행 권한과 결과 회신으로만 성립합니다.
