# BAT-003: TVR quote-only market-data correction

- Status: completed (M5.1 claim accepted)
- Profile: standard
- Root Delivery: `dcy-003_quote-only-market-data`
- Source Discovery: dcy-003
- Included REQ: REQ-003@2

## Scope

Implement quote-only behavior for non-positive `candle_count` inside `KisDataHubClient.fetch_market_data()`. Preserve positive-count retrieval and KST same-day filtering. No strategy, AMQP, external API, production, order, deployment, merge, or release-approval change is in scope.

## Documents

- [Planning](./planning.md)
- [Design](./design.md)
- [Implementation](./implementation.md)
- [Verification](./verification.md)
