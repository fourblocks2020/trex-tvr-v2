"""Universe loader from backtest_universe.csv."""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class UniverseEntry:
    code: str               # KRX 6-digit zero-padded
    name: str
    from_date: Optional[date]  # inclusive, None = unlimited
    to_date: Optional[date]    # inclusive, None = unlimited


def load_universe(path: str | Path) -> list[UniverseEntry]:
    """Parse backtest_universe.csv → list of UniverseEntry.

    code is normalized to 6-digit zero-padded string.
    from/to are parsed as date or None (unlimited).
    """
    entries: list[UniverseEntry] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = str(row.get("code", "")).strip().zfill(6)
            name = str(row.get("name", "")).strip()
            from_date = _parse_date(str(row.get("from", "") or "").strip())
            to_date = _parse_date(str(row.get("to", "") or "").strip())
            entries.append(UniverseEntry(code=code, name=name, from_date=from_date, to_date=to_date))
    return entries


def filter_by_date(entries: list[UniverseEntry], trading_date: date) -> list[str]:
    """Return codes active on trading_date (from/to inclusive; None = unlimited)."""
    result = []
    for e in entries:
        if e.from_date is not None and trading_date < e.from_date:
            continue
        if e.to_date is not None and trading_date > e.to_date:
            continue
        result.append(e.code)
    return result


def _parse_date(value: str) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)  # YYYY-MM-DD
    except ValueError:
        pass
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))  # YYYYMMDD
    except (ValueError, IndexError):
        return None
