"""Exit simulator: bar-touch SL/TP/forced_close, safety-sell semantics."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)
_KST = timezone(timedelta(hours=9))


@dataclass
class ExitRecord:
    exit_price: int
    reason: str   # "stop_loss" | "take_profit" | "forced_close"
    exit_time: str  # ISO-8601 KST
    realized_pnl: int  # after fee + slippage (both sides) + sell tax (sell side), KRW


class ExitSimulator:
    """Replicates trex-core safety-sell semantics via bar-touch simulation.

    Threshold formula mirrors intent_builder.build_buy_intent exactly:
        stop_loss_price   = round(entry * (1 - stop_loss_pct / 100.0))
        take_profit_price = round(entry * (1 + take_profit_pct / 100.0))

    Intra-bar simultaneous SL+TP touch: stop_loss wins (conservative).
    """

    def __init__(
        self,
        stop_loss_pct: float,
        take_profit_pct: float,
        *,
        fee_pct: float = 0.015,       # brokerage fee, both sides (one-way %)
        slippage_pct: float = 0.05,   # estimated slippage, both sides (one-way %)
        sell_tax_pct: float = 0.0,    # KR securities transaction tax — SELL side only (%)
    ) -> None:
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_pct
        self.sell_tax_pct = sell_tax_pct

    def compute_thresholds(
        self, entry_price: int, forced_close_at: Optional[datetime] = None
    ) -> dict[str, Any]:
        """Compute SL/TP/forced_close_at thresholds — identical to intent_builder formula."""
        sl = round(entry_price * (1 - self.stop_loss_pct / 100.0))
        tp = round(entry_price * (1 + self.take_profit_pct / 100.0))
        return {
            "stop_loss_price": sl,
            "take_profit_price": tp,
            "forced_close_at": forced_close_at,
        }

    def simulate(
        self,
        entry_price: int,
        qty: int,
        entry_time: datetime,
        candles: list[dict[str, Any]],
        forced_close_at: Optional[datetime] = None,
    ) -> ExitRecord:
        """Walk-forward candles after entry and find first exit condition.

        If no candle triggers SL/TP/forced_close, uses the last bar's close
        (forced_close fallback).
        """
        thresholds = self.compute_thresholds(entry_price, forced_close_at)
        sl = thresholds["stop_loss_price"]
        tp = thresholds["take_profit_price"]

        for bar in candles:
            bar_dt = _parse_dt(bar.get("datetime"))
            if bar_dt is None:
                continue

            # forced_close check: if bar time >= forced_close_at → forced_close
            if forced_close_at is not None and bar_dt >= forced_close_at:
                return self._make_record(int(float(bar["close"])), "forced_close", bar_dt, entry_price, qty)

            low = int(float(bar.get("low", 0)))
            high = int(float(bar.get("high", 0)))

            # Intra-bar simultaneous touch: stop_loss first (conservative)
            if low <= sl:
                return self._make_record(sl, "stop_loss", bar_dt, entry_price, qty)
            if high >= tp:
                return self._make_record(tp, "take_profit", bar_dt, entry_price, qty)

        # No exit found in remaining bars → use last bar close as forced_close fallback
        if candles:
            last = candles[-1]
            last_dt = _parse_dt(last.get("datetime")) or entry_time
            return self._make_record(int(float(last["close"])), "forced_close", last_dt, entry_price, qty)

        # No bars at all (entered on last bar of day)
        return self._make_record(entry_price, "forced_close", entry_time, entry_price, qty)

    def _make_record(
        self,
        exit_price: int,
        reason: str,
        exit_dt: datetime,
        entry_price: int,
        qty: int,
    ) -> ExitRecord:
        cost_factor = (self.fee_pct + self.slippage_pct) / 100.0
        # Securities transaction tax is charged on the SELL side only (asymmetric),
        # so it is added to the exit-side cost but not the entry cost.
        sell_factor = cost_factor + self.sell_tax_pct / 100.0
        entry_cost = entry_price * qty * (1 + cost_factor)
        exit_revenue = exit_price * qty * (1 - sell_factor)
        realized_pnl = round(exit_revenue - entry_cost)
        return ExitRecord(
            exit_price=exit_price,
            reason=reason,
            exit_time=exit_dt.astimezone(_KST).isoformat(),
            realized_pnl=realized_pnl,
        )


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
