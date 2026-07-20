# BAT-003 Planning

- Status: confirmed
- Batch: BAT-003
- Included REQ: REQ-003@2

1. Branch after quote retrieval: non-positive count returns the unchanged three-key schema with empty candles; positive count retains candle retrieval and filtering.
2. Add mock-transport client evidence for 0, negative, and positive count paths.
3. Add daemon quote-only BuySignal regression coverage.
4. Run targeted/full pytest and independent QC in the isolated worktree.

Risk: regression of positive-count filtering or response shape. Mitigation: explicit positive-count/same-day assertions and full-suite regression.
