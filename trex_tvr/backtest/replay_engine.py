"""Time-replay backtest engine.

Replay engine contract:
- Feeds only past candles to strategy.evaluate() at each tick (look-ahead = 0).
- Per-symbol daily entry limit: once admitted, no re-entry that day.
- Capacity: max simultaneous entries per day.
- Strategy-agnostic: accepts any BuyStrategy subclass.
- NO import from concrete strategies, runtime, transport, or trex_core.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import time, timedelta, timezone
from typing import Any, Optional

from trex_tvr.strategies.base import BuyEvaluation, BuySignal, BuyStrategy
from trex_tvr.backtest.dump_loader import DumpLoader, _parse_dt
from trex_tvr.backtest.exit_simulator import ExitSimulator

logger = logging.getLogger(__name__)
_KST = timezone(timedelta(hours=9))


@dataclass
class TradeRecord:
    symbol: str
    date: str          # YYYYMMDD
    entry_price: int
    entry_time: str    # ISO-8601 KST
    exit_price: int
    exit_time: str     # ISO-8601 KST
    exit_reason: str   # "stop_loss" | "take_profit" | "forced_close"
    qty: int
    realized_pnl: int  # after fee + slippage


@dataclass
class BacktestResult:
    trades: list[TradeRecord]
    metrics: dict[str, Any]
    missing_symbols: dict[str, list[str]] = field(default_factory=dict)


class BacktestEngine:
    """Strategy-agnostic time-replay engine.

    Args:
        strategy: any BuyStrategy subclass.
        dump_loader: DumpLoader for local candle data.
        exit_simulator: ExitSimulator for position exit.
        capacity: max simultaneous (same-day) entries.
        schedule_start: KST time when the strategy schedule opens (default 09:15).
        entry_cutoff: KST time after which no new entry (default 10:30).
    """

    def __init__(
        self,
        strategy: BuyStrategy,
        dump_loader: DumpLoader,
        exit_simulator: ExitSimulator,
        *,
        capacity: int = 10,
        schedule_start: time = time(9, 15),
        entry_cutoff: time = time(10, 30),
    ) -> None:
        self.strategy = strategy
        self.dump_loader = dump_loader
        self.exit_simulator = exit_simulator
        self.capacity = capacity
        self.schedule_start = schedule_start
        self.entry_cutoff = entry_cutoff

    def run_day(
        self,
        symbol: str,
        date: str,
        day_candles: list[dict[str, Any]],
        asset: Optional[dict] = None,
        snapshot: object = None,
    ) -> Optional[TradeRecord]:
        """Replay one (symbol, date) day. Returns TradeRecord if a trade occurred."""
        if asset is None:
            asset = {"symbol": symbol, "entry_capital": 1_000_000, "allocated_capital": 1_000_000}

        for tick_idx, bar in enumerate(day_candles):
            now_kst = _parse_dt(bar.get("datetime"))
            if now_kst is None:
                continue
            now_kst = now_kst.astimezone(_KST)

            bar_date = now_kst.strftime("%Y%m%d")
            bar_time = now_kst.time()

            # Schedule gate: only evaluate within [schedule_start, entry_cutoff)
            if bar_time < self.schedule_start:
                continue
            if bar_time >= self.entry_cutoff:
                break

            if bar_date != date:
                continue  # skip stale candles from previous day

            # Assemble market_data with only candles up to and including current tick
            # (look-ahead prohibition: future bars are NOT visible)
            candles_visible = day_candles[: tick_idx + 1]
            market_data = {
                "current_price": float(bar["close"]),
                "candles": candles_visible,
                "now_kst": now_kst,
            }

            result: BuyEvaluation = self.strategy.evaluate(asset, snapshot, market_data)

            if isinstance(result, BuySignal):
                qty = result.target_qty or 1
                remaining_bars = day_candles[tick_idx + 1 :]
                exit_rec = self.exit_simulator.simulate(
                    entry_price=result.entry_price,
                    qty=qty,
                    entry_time=now_kst,
                    candles=remaining_bars,
                    forced_close_at=result.forced_close_at,
                )
                return TradeRecord(
                    symbol=symbol,
                    date=date,
                    entry_price=result.entry_price,
                    entry_time=now_kst.isoformat(),
                    exit_price=exit_rec.exit_price,
                    exit_time=exit_rec.exit_time,
                    exit_reason=exit_rec.reason,
                    qty=qty,
                    realized_pnl=exit_rec.realized_pnl,
                )

        return None

    def run_multi_day(
        self,
        symbols: list[str],
        date_range: list[str],
        *,
        asset_template: Optional[dict] = None,
        snapshot: object = None,
    ) -> BacktestResult:
        """Run multi-day, multi-symbol backtest with portfolio accounting.

        For each date:
        - Admitted symbols set resets (per-day 1회 진입).
        - Capacity cap: at most `capacity` entries per day.
        - Symbols missing dump data are reported in missing_symbols.
        """
        from trex_tvr.backtest.metrics import compute_metrics

        trades: list[TradeRecord] = []
        missing_symbols: dict[str, list[str]] = {}

        for date in date_range:
            admitted_symbols: set[str] = set()
            entries_today = 0
            missing_today: list[str] = []

            for symbol in symbols:
                if entries_today >= self.capacity:
                    break
                if symbol in admitted_symbols:
                    continue

                day_candles = self.dump_loader.load(symbol, date)
                if not day_candles:
                    missing_today.append(symbol)
                    continue

                asset = dict(asset_template or {})
                if "symbol" not in asset:
                    asset["symbol"] = symbol
                if "entry_capital" not in asset:
                    asset["entry_capital"] = 1_000_000
                if "allocated_capital" not in asset:
                    asset["allocated_capital"] = asset["entry_capital"]

                trade = self.run_day(symbol, date, day_candles, asset=asset, snapshot=snapshot)
                if trade:
                    admitted_symbols.add(symbol)
                    entries_today += 1
                    trades.append(trade)

            if missing_today:
                missing_symbols[date] = missing_today
                logger.info(
                    "backtest: date=%s effective=%d/%d missing=%d",
                    date,
                    len(symbols) - len(missing_today),
                    len(symbols),
                    len(missing_today),
                )

        return BacktestResult(
            trades=trades,
            metrics=compute_metrics(trades),
            missing_symbols=missing_symbols,
        )
