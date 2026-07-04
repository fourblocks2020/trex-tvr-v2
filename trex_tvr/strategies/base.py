"""BuyStrategy ABC + output dataclasses.

Ported from trex-orbit `app/strategies/buy/base.py`. `BuyStrategy.evaluate()`
returns one of `BuySignal | SellSignal | HoldDecision | Diagnostic`:
  - BuySignal   -> emit a buy intent (short-circuits this asset).
  - SellSignal  -> emit a sell intent (partial rebalancing sell).
  - HoldDecision-> no decision; move to the next asset.
  - Diagnostic  -> input cannot be trusted; audit and move on.

Contract:
  - `evaluate()` is deterministic: same (asset, snapshot, market_data) -> same output.
  - No side effects (no IO, no time.now(), no random).
  - `snapshot` is read-only.
  - This module imports nothing from transport/market_data/redis/pika/httpx.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Union


@dataclass
class BuySignal:
    """Strategy decided to buy this asset.

    `entry_price` is the strategy's decision-time price (used by the runtime
    intent builder to derive exit thresholds). `forced_close_at` is the absolute
    forced-close instant sealed at decision time (today @ forced_close_time).
    """

    symbol: str
    target_qty: int
    allocated_capital: int
    reason: str
    strategy_id: str
    entry_price: int = 0
    forced_close_at: Optional[datetime] = None


@dataclass
class SellSignal:
    """Strategy decided to partially sell this asset (rebalancing sell).

    `qty` is the partial sell quantity. This signal drives a sell intent via
    the transport layer — it is NOT a full exit. trex-core keeps the slot HOLDING
    after a partial fill.
    """

    symbol: str
    slot_id: str
    qty: int
    reason: str
    strategy_id: str


@dataclass
class HoldDecision:
    """Strategy produced no buy/sell decision. Move to the next asset/strategy."""

    symbol: str
    reason: str
    strategy_id: str


@dataclass
class Diagnostic:
    """Strategy cannot trust its input data (e.g. range_data_missing, stale_quote)."""

    symbol: str
    reason: str
    strategy_id: str
    details: Optional[dict[str, Any]] = field(default=None)


# Type alias for the evaluate() return value.
BuyEvaluation = Union[BuySignal, SellSignal, HoldDecision, Diagnostic]


class BuyStrategy(ABC):
    """Plug-in BuyStrategy ABC. Subclasses set `strategy_id` and implement `evaluate`."""

    # Default class attribute; registry resolver overrides per instance to match
    # the registry key (supports one class under multiple keys with different params).
    strategy_id: str = ""

    # Number of recent minute candles the runtime fetches per asset into
    # market_data["candles"] before each evaluate(). market_data always carries
    # `candles`; ignore the key if your strategy only needs `current_price`.
    candle_count: int = 400

    @abstractmethod
    def evaluate(self, asset: dict, snapshot: object, market_data: dict) -> BuyEvaluation:
        """Evaluate this strategy for a single asset. Pure and deterministic."""
