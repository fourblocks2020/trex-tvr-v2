"""Buy/sell intent payload builders.

TVR declares the execution venue explicitly on every buy/sell intent payload.
The runtime session gate chooses `venue="NXT"` for NXT-only windows and
`venue="KRX"` for the main KRX session.

exit is delegated to trex-core's safety-sell by declaring stop_loss / take_profit /
forced_close thresholds on the buy intent. TVR's own rebalancing sell path carries
only slot, quantity, reason, strategy identity, venue, and a decision-cycle
idempotency key.

REL-006 canonical target key is `slot_id` (slots[] round-trip). The
buy payload carries `slot_id` and does NOT carry the deprecated `portfolio_id`,
`symbol`, `trading_date`, or `allocated_capital` keys. trex-core resolves
the symbol from the universe at admission.

Buy idempotency remains deterministic per slot and trading day. Sell idempotency
also includes the decision-cycle timestamp: duplicate transport submission of one
decision keeps the same key, while a later core snapshot can produce an independent
strategy decision without consuming broker retry metadata.

Optional execution-policy fields (REL-006) are included only when
configured: `timeout_sec` (auto-cancel + slot rollback after N seconds unfilled) and
`slippage_pct` (protective limit band). When unset they are omitted, preserving
byte-equivalent backward compatibility with core's default behavior.
"""
from __future__ import annotations

from trex_tvr.strategies.base import BuySignal, SellSignal

_VALID_VENUES = {"KRX", "NXT"}


def _require_venue(venue: str | None) -> str:
    if venue not in _VALID_VENUES:
        raise ValueError("venue must be one of: 'KRX', 'NXT'")
    return venue


def make_intent_id(
    strategy_id: str,
    trading_date: str,
    slot_id: str,
    instance_key: str = "default",
) -> str:
    """Deterministic idempotency key — one buy admission per (instance, slot) per trading day.

    BAT-002 REQ-007: instance_key added to prevent cross-instance collision.
    Format: `{strategy_id}:{instance_key}:{trading_date}:{slot_id}`
    """
    return f"{strategy_id}:{instance_key}:{trading_date}:{slot_id}"


def make_sell_intent_id(
    strategy_id: str,
    trading_date: str,
    slot_id: str,
    instance_key: str = "default",
    decision_at: str = "",
) -> str:
    """Deterministic key for one sell decision cycle.

    ``decision_at`` comes from the current decision cycle, not from a broker result.
    This preserves transport idempotency for one decision while allowing a later
    holding snapshot to produce a new independent intent.
    """
    if not decision_at:
        raise ValueError("decision_at is required for a sell intent id")
    return f"sell:{strategy_id}:{instance_key}:{trading_date}:{slot_id}:{decision_at}"


def build_buy_intent(
    signal: BuySignal,
    *,
    trading_date: str,
    slot_id: str,
    stop_loss_pct: float,
    take_profit_pct: float,
    strategy_id: str,
    venue: str | None = None,
    instance_key: str = "default",
    timeout_sec: int | None = None,
    slippage_pct: float | None = None,
    sell_strategy_id: str | None = None,
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
    venue = _require_venue(venue)
    entry = float(signal.entry_price)
    payload: dict = {
        "intent_type": "buy",
        "intent_id": make_intent_id(strategy_id, trading_date, slot_id, instance_key),
        "slot_id": slot_id,
        "qty": int(signal.target_qty),
        "entry_price": int(signal.entry_price),
        "strategy_id": strategy_id,
        "venue": venue,
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


def build_sell_intent(
    signal: SellSignal,
    *,
    trading_date: str,
    slot_id: str,
    strategy_id: str,
    venue: str | None = None,
    instance_key: str = "default",
    decision_at: str,
) -> dict:
    """Map a SellSignal into a sell intent payload for trex-core.

    Sell intent fields (trex-core SellIntent wire contract):
      intent_type       "sell"
      intent_id         sell:{strategy_id}:{trading_date}:{slot_id}  (멱등 키)
      slot_id           slot to partially sell
      qty               partial sell quantity (> 0)
      reason            sell reason (e.g. "rebalance_sell")
      sell_strategy_id  strategy that emitted this sell

    stop_loss_price / take_profit_price / forced_close_at are intentionally absent —
    TVR is a pure rebalancing strategy; exit thresholds are not declared on sell intents.
    """
    if not slot_id:
        raise ValueError("slot_id is required for a sell intent")
    if not isinstance(signal.qty, int) or signal.qty <= 0:
        raise ValueError(f"sell intent qty must be a positive int (got {signal.qty!r})")
    venue = _require_venue(venue)
    return {
        "intent_type": "sell",
        "intent_id": make_sell_intent_id(
            strategy_id, trading_date, slot_id, instance_key, decision_at
        ),
        "slot_id": slot_id,
        "qty": int(signal.qty),
        "reason": signal.reason,
        "sell_strategy_id": strategy_id,
        "venue": venue,
    }
