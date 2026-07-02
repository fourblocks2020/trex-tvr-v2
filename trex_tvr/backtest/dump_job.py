"""Dump job: kis-data-hub → local candle dump.

This module is the ONLY place where kis-data-hub is called for backtest purposes.
Run this as a standalone step AFTER the trading day closes (after 15:40 KST flush).
The backtest replay engine (replay_engine.py) reads local dump only — no live calls.

Usage (example):
    from trex_tvr.backtest.dump_job import run_dump_job
    run_dump_job(
        universe=["005930", "000660"],
        date_range=["20260101", "20260612"],
        dump_dir="data/dump",
        fetch_fn=lambda code, as_of, count: client.get_candles_as_of(code, as_of, count),
    )
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Default candle count per day (400 covers a full trading day at 1-min resolution + buffer)
DEFAULT_CANDLE_COUNT = 400


def run_dump_job(
    universe: list[str],
    date_range: list[str],
    dump_dir: str | Path,
    fetch_fn: Callable[[str, str, int], list[dict[str, Any]]],
    *,
    candle_count: int = DEFAULT_CANDLE_COUNT,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Download and write local dump for (universe × date_range).

    Args:
        universe: list of KRX 6-digit codes.
        date_range: list of YYYYMMDD strings (trading days only; holidays skipped naturally).
        dump_dir: root dir for dump files ({dump_dir}/{symbol}/{date}.csv).
        fetch_fn: callable(code, as_of, count) → list[candle dict].
                  as_of is "{date}T15:30" (KST) to capture full day.
                  Raise on error; return [] if no data.
        candle_count: bars to request per (code, date) call.
        overwrite: if False (default), skip (code, date) where file already exists.

    Returns:
        dict with keys: written, skipped, failed, errors.
    """
    dump_root = Path(dump_dir)
    written = 0
    skipped = 0
    failed = 0
    errors: list[str] = []

    for date in date_range:
        as_of = f"{date[:4]}-{date[4:6]}-{date[6:8]}T15:30"
        for code in universe:
            dest = dump_root / code / f"{date}.csv"
            if not overwrite and dest.exists():
                skipped += 1
                continue
            try:
                bars = fetch_fn(code, as_of, candle_count)
                if bars:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    _write_csv(dest, bars)
                    written += 1
                    logger.info("dump: wrote %s/%s (%d bars)", code, date, len(bars))
                else:
                    skipped += 1
                    logger.debug("dump: no bars for %s/%s", code, date)
            except Exception as exc:
                failed += 1
                msg = f"{code}/{date}: {exc}"
                errors.append(msg)
                logger.warning("dump: failed %s", msg)

    return {"written": written, "skipped": skipped, "failed": failed, "errors": errors}


def _write_csv(path: Path, bars: list[dict[str, Any]]) -> None:
    fields = ["datetime", "open", "high", "low", "close", "volume"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(bars)
