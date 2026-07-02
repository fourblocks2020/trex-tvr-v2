"""Download historical minute candles into a local dump for backtesting.

    python scripts/run_dump.py [--from YYYYMMDD] [--to YYYYMMDD]

Reads KIS_DATA_HUB_BASE_URL / KIS_DATA_HUB_API_KEY from config/.env, the universe
from config/backtest_universe.csv, and writes data/dump/{code}/{date}.csv
(skips existing files unless --overwrite). Strategy-agnostic.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from trex_tvr.backtest.dump_job import run_dump_job
from trex_tvr.backtest.universe import load_universe
from trex_tvr.market_data.kis_data_hub_client import KisDataHubClient, KisDataHubClientError


def _load_env(env_file: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not env_file.exists():
        return env
    for line in open(env_file):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def _weekdays(from_date: str, to_date: str) -> list[str]:
    start = date(int(from_date[:4]), int(from_date[4:6]), int(from_date[6:8]))
    end = date(int(to_date[:4]), int(to_date[4:6]), int(to_date[6:8]))
    days, cur = [], start
    while cur <= end:
        if cur.weekday() < 5:  # Mon-Fri
            days.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return days


def main() -> None:
    parser = argparse.ArgumentParser(description="Download historical candle dump")
    parser.add_argument("--from", dest="from_date", required=True, metavar="YYYYMMDD")
    parser.add_argument("--to", dest="to_date", required=True, metavar="YYYYMMDD")
    parser.add_argument("--dump-dir", default="data/dump")
    parser.add_argument("--universe", default="config/backtest_universe.csv")
    parser.add_argument("--count", type=int, default=400, help="candle bars per request")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).parent.parent
    env = _load_env(root / "config" / ".env")
    base_url = env.get("KIS_DATA_HUB_BASE_URL") or os.environ.get("KIS_DATA_HUB_BASE_URL", "")
    api_key = env.get("KIS_DATA_HUB_API_KEY") or os.environ.get("KIS_DATA_HUB_API_KEY", "")
    if not base_url:
        print("ERROR: KIS_DATA_HUB_BASE_URL not set", file=sys.stderr)
        sys.exit(1)

    symbols = [e.code for e in load_universe(root / args.universe)]
    date_range = _weekdays(args.from_date, args.to_date)
    print(f"Dumping {len(symbols)} symbols x {len(date_range)} days -> {root / args.dump_dir}")

    client = KisDataHubClient(base_url, api_key)

    def fetch_fn(code: str, as_of: str, count: int) -> list[dict]:
        # as_of = "YYYY-MM-DDTHH:MM"
        try:
            return client.get_candles_historical(code, count, date=as_of[:10], time=as_of[11:16])
        except KisDataHubClientError:
            return []

    result = run_dump_job(
        universe=symbols,
        date_range=date_range,
        dump_dir=root / args.dump_dir,
        fetch_fn=fetch_fn,
        candle_count=args.count,
        overwrite=args.overwrite,
    )
    client.close()
    print(f"Done: written={result['written']} skipped={result['skipped']} failed={result['failed']}")


if __name__ == "__main__":
    main()
