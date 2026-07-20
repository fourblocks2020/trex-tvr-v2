# REL-002: TVR quote-only market-data correction

- Status: draft — release approval pending
- Profile: app-service
- Included batch: BAT-003
- Scope authority: DCY-003 confirmed; REQ-003@2 approved
- Candidate code/test payload: `9398b2d`
- Publication state: merged on local `main`; remote push is pending. No deployment or production action has occurred.

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

## Release controls

This is a **draft** only. It does not approve publication, merge, deployment, or production validation. Those actions remain blocked pending explicit user release approval and the normal merge/publication process.
