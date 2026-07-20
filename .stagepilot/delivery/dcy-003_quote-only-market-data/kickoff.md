# DCY-003 delivery kickoff — quote-only market data

- Root delivery ID: `dcy-003_quote-only-market-data`
- Owner target: `delivery-runner`
- Delivery profile: `standard`
- Bound scope: `REQ-003@2`
- Immutable scope snapshot: `scope-REQ-003-r2.json`
- Scope digest: `cd69901adf9182dfcb37638ec47760ce0205c08aad6186e5566559648bc82b41`
- Approved source: `dcy-003` confirmed by the requester on 2026-07-20; `REQ-003@2` approved through `CHG-20260720-02`.

## Execution mandate

1. Read `state.json` and the bound scope snapshot before any work. The snapshot is immutable delivery authority.
2. Create and execute one standard batch for `REQ-003@2`; create planning and design artifacts before implementation.
3. Implement only the quote-only correction and its regression tests. Keep all code, tests, commits, and delivery artifacts inside the isolated runner worktree.
4. Use canonical implementation and independent QC wrappers. Do not waive QC for this standard delivery.
5. Maintain AC-level verification evidence, batch verification, and REQ synchronization. Do not mark the root done without those artifacts.
6. The runner may not change the approved scope, merge or publish, deploy, run real orders, or create/approve a release. On successful release-candidate evidence, hand back to the lead at the release-draft gate with the exact artifact paths, test results, QC outcome, and residual risks.

## Bound non-goals

- No strategy-formula, AMQP, trex-core, or kis-data-hub API-contract changes.
- No production connectivity, deployment, real-order, push, PR merge, or release approval.

## Expected hand-back

A terminal root state (`done` or explicit `blocked`) with enough current evidence for the lead to independently determine whether a draft release can be created. A runner `done` state alone is not a release claim.
