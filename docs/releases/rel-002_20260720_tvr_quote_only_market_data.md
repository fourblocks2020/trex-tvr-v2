# REL-002: TVR quote-only market-data correction

- Status: feedback-captured — lifecycle claim unverified
- Profile: app-service
- Included batch: BAT-003
- Scope authority: DCY-003 confirmed; REQ-003@2 approved
- Candidate code/test payload: `9398b2d`
- Publication state: merged and pushed to `origin/main`; operations evidence reports deployment revision `e415052e3697a4160a07f5c8962ad8acbd58d6e8`.

## Change summary

Correct `KisDataHubClient.fetch_market_data()` so `candle_count <= 0` performs quote-only retrieval and returns the unchanged schema with `candles=[]`. Positive-count minute-candle retrieval and KST same-day filtering remain unchanged. The daemon regression confirms an eligible quote-only BuySignal submits one buy intent without a minute-candle request.

## Evidence

| Gate | Evidence | Result |
|---|---|---|
| Targeted regression | `env -u PYTHONPATH .venv/bin/pytest -q tests/market_data/test_kis_data_hub_client.py tests/runtime/test_daemon_cycle.py` | 19 passed |
| Full regression | `env -u PYTHONPATH .venv/bin/pytest -q` | 158 passed |
| Source hygiene | `git diff --check`; targeted Ruff | pass; `All checks passed!` |
| Independent QC | `dev-qc` fresh review | PASS-WITH-NOTES; no product-code blocker |
| Batch verification | BAT-003 verification gate | confirmed |

## Risk and rollback

- Product risk: low and bounded to the existing market-data client branch. The branch prevents invalid minute-candle calls for non-positive counts while retaining positive-count behavior.
- Rollback: revert payload commit `9398b2d` before deployment. No data migration, external contract change, or production state change is involved.
- Residual operational note: two supervised implementation workers were stopped after primary-checkout write attempts; a third completed the isolated-worktree changes but omitted its canonical final-result artifact after compaction. Independent QC reran evidence directly and passed. This is a harness-integrity follow-up, not a product failure.

## Feedback Handoff

### 운영 관찰 결과

운영팀 제공 증적(2026-07-20 14:52:28 KST)은 `trex-tvr-v1`만 production에 갱신·재기동했으며, deployed revision을 `e415052e3697a4160a07f5c8962ad8acbd58d6e8`로 보고한다. 구현 payload `9398b2d9112db2b6388abeb671957843fc7bc57a`는 해당 revision의 ancestor로 보고됐다.

- 배포 후 TVR는 RUNNING(PID `2166495`)이며 RabbitMQ 연결, kis-data-hub health, Redis ping이 정상으로 보고됐다.
- 첫 정상 cycle은 `2026-07-20 14:48:09 KST`에 `status=ok`, `intents=1`, `diagnostics=0`, `elapsed=94ms`로 보고됐다. 다음 cycle도 `status=ok`, `intents=0`, `diagnostics=0`이었다.
- 운영팀은 배포 후 quote-only 경로의 minute-candle HTTP 400이 재관측되지 않았고, 005930 BUY 1주와 portfolio-hub ledger 반영을 확인했다고 보고했다.
- 운영 release venv에는 pytest 실행 모듈이 없어 targeted/full pytest는 재실행하지 못했다. 사전 후보 검증의 targeted `19 passed`, full `158 passed`, diff-check·targeted Ruff 통과, independent QC `PASS-WITH-NOTES`가 근거로 유지된다.
- portfolio-hub realized PnL WAC 조회의 기존 서비스 토큰 403 경고는 남아 있으며, 운영팀은 quote-only 배포 및 BUY ledger 검증의 blocker가 아니라고 보고했다.

### 후속 입력

- **Discovery Input:** 배포 요청서의 기준 SHA와 실제 배포 SHA 불일치를 release artifact 및 승인 절차에서 명시적으로 처리할지 검토한다.
- **REQ Input:** 없음.
- **Change Request Input:** 없음.

### Lifecycle 기록 경계

운영팀 증적은 이 문서에 사실로 보존한다. 그러나 현재 환경에서 필수 `scripts/record_lifecycle_claim.py`를 확인하지 못했으므로, 이 문서만으로 positive lifecycle claim(`deployed` 또는 `release_completion`)을 기록하거나 승격하지 않는다.

## Release controls

Operations evidence is captured in the Feedback Handoff above. The positive lifecycle claim remains unverified because the required lifecycle-claim recorder was unavailable in this environment.
