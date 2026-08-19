# GitHub issue #5 — 구현 진행 상황 comment snapshot

- Source: https://github.com/fourblocks2020/trex-tvr-v1/issues/5#issuecomment-5337521236
- Author: `genichin`
- Created: `2026-08-19T04:26:09Z`
- Updated: `2026-08-19T04:39:20Z`
- Classification: repository-local implementation evidence; not a canonical ProofLine lifecycle artifact

---

## 구현 진행 상황

- **대상:** [trex-tvr-v1] bug: retryable sell rejection reuses same intent ID and never resubmits
- **브랜치:** `main`
- **기준:** `main` (`c0cec28ed69c81331f81257e36259754b5128d4c`)
- **상태:** 로컬 main 구현·검증·커밋 및 독립 QC PASS — 미푸시, 미배포
- **최종 업데이트:** 2026-08-19 13:39:20 KST

### 구현 계획 및 작업 목록

- [x] 거래 시각을 NXT 전용 구간, KRX 본장, 경매 공백, 장외 시간으로 정확히 구분한다.
  - 휴장일과 두 경매 공백에서는 매수·매도 주문 판단을 모두 중단한다.
- [x] NXT 전용 구간에는 NXT 거래가 가능한 slot만 매수·매도 판단 대상으로 사용한다.
  - slot의 `markets`가 없거나 NXT를 포함하지 않으면 intent RPC를 만들지 않고 이해할 수 있는 diagnostic을 남긴다.
- [x] 허용된 slot의 매수·매도 intent를 venue 없이 core에 전달한다.
  - 실제 주문 venue는 core #61의 누락 venue→SOR 계약에 맡기고 TVR이 KRX/NXT를 지정하지 않는다.
- [x] TVR이 core의 다음 slot snapshot만으로 후속 전략 판단을 수행하도록 한다.
  - KIS 실패, broker 거부 사유, attempt ID, retryability, timeout, 체결·취소 원시 이벤트를 받거나 저장하지 않는다.
- [x] 자동 재시도와 durable retry 상태가 TVR에 추가되지 않도록 실행 경계를 검증한다.
  - sell 미수락·미체결과 결과 불명확 상태의 수렴·reconciliation은 core가 소유한다.
- [x] core #60의 no-auto-retry 및 core #61의 omitted-venue→SOR 계약과 wire 호환성을 검증한다.
- [x] import seam, 관련 집중 테스트와 전체 TVR 회귀를 실행한다.
  - 검증 중 승인된 계획의 의미나 범위를 바꿔야 하면 즉시 중지하고 사용자 재승인을 요청한다.

### 진행 기록

- 2026-08-19 13:25:40 KST — 승인된 issue #5 본문과 local `main` 기준 SHA를 확인했다. 별도 구현 worktree나 기능 branch를 만들지 않고 `main`에서 구현·검증을 시작한다.
- 2026-08-19 13:25:40 KST — 작업 시작 전부터 존재하던 `.stagepilot/` 및 `.worktrees/`의 untracked artifact는 이번 구현 범위 밖으로 보존하고 수정·삭제하지 않는다.

- 2026-08-19 13:32:51 KST — session 경계의 승인값을 대조해 NXT-only 종료를 `20:00:00` 미만으로 수정하고, NXT-only `markets` gate가 buy/sell 공통 경로에 적용됨을 확인했다.
- 2026-08-19 13:32:51 KST — sell intent ID를 broker retry 정보가 아니라 snapshot decision cycle 시각으로 구분했다. 같은 decision의 idempotency는 유지하고 다음 holding snapshot은 독립 intent를 생성한다.
- 2026-08-19 13:32:51 KST — runtime에서 broker/fill event consumer wiring과 polling을 제거했다. TVR은 slot snapshot만 판단 입력으로 사용하며 intent payload에는 `venue`를 추가하지 않는다.
- 2026-08-19 13:32:51 KST — session·markets·sell decision ID·snapshot-only·wire·import seam 집중 테스트 `86 passed`.

- 2026-08-19 13:34:01 KST — current README·interface contract·data model·project structure를 snapshot-only, venue omission, decision-cycle sell idempotency 계약으로 정합화했다.
- 2026-08-19 13:34:01 KST — 전체 회귀 `160 passed`, 변경 코드·테스트 Ruff PASS, diff check PASS. local main commit `cc986dfe606f6f00c25119add7c9da4381239f3a`을 생성했다.
- 2026-08-19 13:34:01 KST — 작업 전부터 존재한 untracked `.stagepilot/`·`.worktrees/` artifact는 그대로 보존했으며 이번 commit에 포함하지 않았다. push·PR·배포·릴리스·실주문은 수행하지 않았다. 독립 read-only QC를 요청한다.

- 2026-08-19 13:37:37 KST — 독립 read-only QC PASS: blocker 0, major 0, minor 2. 동작 결함은 없었고 legacy event/module·project gap·historical 20:00 문서 설명과 full-tree Ruff baseline을 확인했다.
- 2026-08-19 13:37:37 KST — 승인 계획의 문서·검증 범위 안에서 legacy event module을 명시하고 current project gap을 정정했으며 historical BAT에 20:00 exclusive superseded note를 추가했다. buy venue omission, holding/sell NXT gate, build_daemon no-event-wiring regression을 보강했다.
- 2026-08-19 13:37:37 KST — 보완 후 전체 `162 passed`, 변경 범위 Ruff·diff check PASS, commit `bce674974b1b43f4763c22f7be431807920d714c`. full-tree Ruff의 범위 밖 기존 오류와 untracked artifact는 변경하지 않았다. 독립 재QC를 요청한다.

- 2026-08-19 13:39:20 KST — 독립 재QC PASS: blocker 0, major 0, minor 0. 추가 회귀 3 passed, runtime 19 passed, 전체 162 passed, 변경 범위 Ruff·diff check PASS. 최종 HEAD `bce674974b1b43f4763c22f7be431807920d714c`, tracked/index clean. 기존 untracked artifact는 보존했다.

> 각 작업이 실제로 완료되고 검증될 때마다 이 댓글의 체크박스와 진행 기록을 갱신한다. 체크 표시는 구현 완료 또는 해당 검증의 실제 통과를 의미하며, 배포·릴리스 완료를 의미하지 않는다.
