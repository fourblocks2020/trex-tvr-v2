"""buy intent payload builder + exit threshold mapping.

exit is delegated to trex-core's safety-sell by declaring stop_loss / take_profit /
forced_close thresholds on the buy intent. this daemon NEVER builds or publishes a
sell intent — there is intentionally no sell path in this module.

REL-006 canonical target key is `slot_id` (slots[] round-trip). The
buy payload carries `slot_id` and does NOT carry the deprecated `portfolio_id`,
`symbol`, `trading_date`, or `allocated_capital` keys. trex-core resolves
the symbol from the universe at admission.

The idempotency key (`intent_id` == wire `correlation_id`) is deterministic per
(strategy_id, trading_date, slot_id), so a retried cycle on the same trading day
reuses the same key and trex-core's intent_log dedups the duplicate admission
(the trading-date component keeps one admission per slot per day; the next
trading day yields a fresh key).

Optional execution-policy fields (REL-006) are included only when
configured: `timeout_sec` (auto-cancel + slot rollback after N seconds unfilled) and
`slippage_pct` (protective limit band). When unset they are omitted, preserving
byte-equivalent backward compatibility with core's default behavior.
"""
from __future__ import annotations

from typing import Optional

from trex_tvr.strategies.base import BuySignal


def make_intent_id(strategy_id: str, trading_date: str, slot_id: str) -> str:
    """Deterministic idempotency key — one buy admission per slot per trading day."""
    return f"{strategy_id}:{trading_date}:{slot_id}"


def build_buy_intent(
    signal: BuySignal,
    *,
    trading_date: str,
    slot_id: str,
    stop_loss_pct: float,
    take_profit_pct: float,
    strategy_id: str,
    timeout_sec: Optional[int] = None,
    slippage_pct: Optional[float] = None,
    sell_strategy_id: Optional[str] = None,
) -> dict:
    """Map a BuySignal + exit pct into a REL-006 buy intent payload.

    The target is identified by `slot_id` (required, REL-006 canonical key); the
    deprecated `portfolio_id` / `symbol` / `trading_date` / `allocated_capital` keys
    are intentionally absent.

    stop_loss_price  = round(entry * (1 - stop_loss_pct/100))
    take_profit_price= round(entry * (1 + take_profit_pct/100))
    forced_close_at  = BuySignal.forced_close_at (ISO-8601) when present
    timeout_sec / slippage_pct = optional execution policy (omitted when None)
    """
    if not slot_id:
        raise ValueError("slot_id is required for a buy intent (REL-006 canonical key)")
    entry = float(signal.entry_price)
    payload: dict = {
        "intent_type": "buy",
        "intent_id": make_intent_id(strategy_id, trading_date, slot_id),
        "slot_id": slot_id,
        "qty": int(signal.target_qty),
        "entry_price": int(signal.entry_price),
        "strategy_id": strategy_id,
        "stop_loss_price": round(entry * (1 - stop_loss_pct / 100.0)),
        "take_profit_price": round(entry * (1 + take_profit_pct / 100.0)),
    }
    if signal.forced_close_at is not None:
        payload["forced_close_at"] = signal.forced_close_at.isoformat()
    if timeout_sec is not None:
        payload["timeout_sec"] = int(timeout_sec)
    if slippage_pct is not None:
        payload["slippage_pct"] = float(slippage_pct)
    if sell_strategy_id is not None:
        payload["sell_strategy_id"] = sell_strategy_id
    return payload
