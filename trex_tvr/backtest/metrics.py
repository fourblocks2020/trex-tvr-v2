"""Backtest metrics computation and output."""
from __future__ import annotations

import csv
import statistics
from collections import Counter
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from trex_tvr.backtest.replay_engine import TradeRecord


def compute_metrics(trades: list["TradeRecord"]) -> dict[str, Any]:
    """Compute P&L, win rate, MDD, average hold time from completed trades."""
    if not trades:
        return {
            "total_trades": 0,
            "total_pnl": 0,
            "win_rate": 0.0,
            "max_drawdown": 0,
            "avg_hold_minutes": 0.0,
            "avg_return_pct": 0.0,
            "avg_entries_per_day": 0.0,
            "entries_per_day_std": 0.0,
            "entries_per_day_max": 0,
        }

    total_pnl = sum(t.realized_pnl for t in trades)
    wins = sum(1 for t in trades if t.realized_pnl > 0)
    win_rate = wins / len(trades)

    # Max drawdown: peak-to-trough on cumulative P&L
    cumulative = 0
    peak = 0
    max_drawdown = 0
    for t in trades:
        cumulative += t.realized_pnl
        if cumulative > peak:
            peak = cumulative
        drawdown = peak - cumulative
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    # Average hold time in minutes
    hold_minutes: list[float] = []
    for t in trades:
        try:
            from datetime import datetime
            entry_dt = datetime.fromisoformat(t.entry_time)
            exit_dt = datetime.fromisoformat(t.exit_time)
            delta = (exit_dt - entry_dt).total_seconds() / 60.0
            hold_minutes.append(abs(delta))
        except (ValueError, TypeError):
            pass
    avg_hold = sum(hold_minutes) / len(hold_minutes) if hold_minutes else 0.0

    # Average per-entry return = mean of realized_pnl / invested capital, where
    # invested = entry_price * qty. realized_pnl is net of whatever costs the
    # ExitSimulator was given (fee + slippage both sides, sell tax on the sell side).
    returns: list[float] = []
    for t in trades:
        invested = t.entry_price * t.qty
        if invested > 0:
            returns.append(t.realized_pnl / invested)
    avg_return_pct = (sum(returns) / len(returns) * 100.0) if returns else 0.0

    # Daily entry-count distribution over ACTIVE trading days (days with >=1 entry).
    # The mean is the distribution center; std + max describe spread. Use these to
    # size max holding slots (e.g. capacity ~= mean + 2*std covers ~95-97%).
    # NOTE: per-day counts are censored by the run's --capacity, so size slots from
    # a run with a large --capacity to observe true daily demand.
    day_counts = list(Counter(t.date for t in trades).values())
    avg_entries_per_day = sum(day_counts) / len(day_counts) if day_counts else 0.0
    entries_per_day_std = statistics.pstdev(day_counts) if len(day_counts) > 1 else 0.0
    entries_per_day_max = max(day_counts) if day_counts else 0

    return {
        "total_trades": len(trades),
        "total_pnl": total_pnl,
        "win_rate": round(win_rate, 4),
        "max_drawdown": max_drawdown,
        "avg_hold_minutes": round(avg_hold, 2),
        "avg_return_pct": round(avg_return_pct, 4),
        "avg_entries_per_day": round(avg_entries_per_day, 2),
        "entries_per_day_std": round(entries_per_day_std, 2),
        "entries_per_day_max": entries_per_day_max,
    }


def format_text_report(trades: list["TradeRecord"], metrics: dict[str, Any]) -> str:
    """Format a human-readable backtest summary."""
    lines = [
        "=== Backtest Result ===",
        f"Trades     : {metrics['total_trades']}",
        f"Total P&L  : {metrics['total_pnl']:,} KRW",
        f"Win Rate   : {metrics['win_rate']:.1%}",
        f"Max DD     : {metrics['max_drawdown']:,} KRW",
        f"Avg Hold   : {metrics['avg_hold_minutes']:.1f} min",
        f"Avg Return : {metrics['avg_return_pct']:.2f}% / entry "
        f"(net of modeled fee/slippage/tax)",
        f"Entries/Day: mean={metrics['avg_entries_per_day']:.1f} "
        f"std={metrics['entries_per_day_std']:.1f} max={metrics['entries_per_day_max']} "
        f"(active days; raise --capacity to uncensor)",
        "",
        f"{'Symbol':<10} {'Date':<10} {'Entry':>8} {'Exit':>8} {'Reason':<15} {'P&L':>10}",
        "-" * 70,
    ]
    for t in trades:
        lines.append(
            f"{t.symbol:<10} {t.date:<10} {t.entry_price:>8,} {t.exit_price:>8,} "
            f"{t.exit_reason:<15} {t.realized_pnl:>10,}"
        )
    return "\n".join(lines)


def write_csv_report(trades: list["TradeRecord"], output_path: str) -> None:
    """Write trade records to CSV file."""
    fields = [
        "symbol", "date", "entry_price", "entry_time", "exit_price",
        "exit_time", "exit_reason", "qty", "realized_pnl",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for t in trades:
            writer.writerow({
                "symbol": t.symbol,
                "date": t.date,
                "entry_price": t.entry_price,
                "entry_time": t.entry_time,
                "exit_price": t.exit_price,
                "exit_time": t.exit_time,
                "exit_reason": t.exit_reason,
                "qty": t.qty,
                "realized_pnl": t.realized_pnl,
            })
