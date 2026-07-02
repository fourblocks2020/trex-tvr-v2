"""Local candle dump loader for backtest replay."""
from __future__ import annotations

import csv
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)
_KST = timezone(timedelta(hours=9))


class DumpLoader:
    """Reads local candle dump for (symbol, date).

    File layout: {dump_dir}/{symbol}/{date}.csv  (or .parquet if pandas available)
    Returns oldest-first, date-filtered 1-minute bars.
    Missing dump or empty → empty list.
    """

    def __init__(self, dump_dir: str | Path) -> None:
        self._dump_dir = Path(dump_dir)

    def load(self, symbol: str, date: str) -> list[dict[str, Any]]:
        """Return minute bars for (symbol, date), oldest-first, today-only.

        date: YYYYMMDD string.
        Returns [] when no dump found or all bars filtered out.
        """
        raw: Optional[list[dict]] = self._try_parquet(symbol, date)
        if raw is None:
            raw = self._try_csv(symbol, date)
        if raw is None:
            return []

        result = []
        for bar in raw:
            dt = _parse_dt(bar.get("datetime"))
            if dt is None:
                continue
            bar_date = dt.astimezone(_KST).strftime("%Y%m%d")
            if bar_date != date:
                continue  # drop previous-day bars (당일 필터)
            try:
                result.append({
                    "datetime": dt.astimezone(_KST).isoformat(),
                    "open": int(float(bar["open"])),
                    "high": int(float(bar["high"])),
                    "low": int(float(bar["low"])),
                    "close": int(float(bar["close"])),
                    "volume": int(float(bar["volume"])),
                })
            except (TypeError, ValueError, KeyError):
                continue

        result.sort(key=lambda b: b["datetime"])  # oldest-first
        return result

    def _try_parquet(self, symbol: str, date: str) -> Optional[list[dict]]:
        path = self._dump_dir / symbol / f"{date}.parquet"
        if not path.exists():
            return None
        try:
            import pandas as pd  # optional dep
            df = pd.read_parquet(path)
            return df.to_dict("records")
        except Exception as exc:
            logger.debug("parquet load failed for %s/%s: %s", symbol, date, exc)
            return None

    def _try_csv(self, symbol: str, date: str) -> Optional[list[dict]]:
        path = self._dump_dir / symbol / f"{date}.csv"
        if not path.exists():
            return None
        try:
            rows: list[dict] = []
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(dict(row))
            return rows
        except Exception as exc:
            logger.warning("csv load failed for %s/%s: %s", symbol, date, exc)
            return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
