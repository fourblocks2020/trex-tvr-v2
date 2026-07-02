"""Pure decision layer: BuyStrategy contract + your strategy + registry.

This package MUST NOT import infrastructure (transport, market_data, redis, pika,
httpx). `evaluate()` is a pure, deterministic function of its injected arguments.
Enforced by tests/test_import_sweep.py — this seam is what makes the template reusable.
"""

from trex_tvr.strategies.base import (
    BuyEvaluation,
    BuySignal,
    BuyStrategy,
    Diagnostic,
    HoldDecision,
)

__all__ = [
    "BuyEvaluation",
    "BuySignal",
    "BuyStrategy",
    "Diagnostic",
    "HoldDecision",
]
