"""trex-tvr-backtest CLI — strategy-agnostic time-replay backtest.

Works for any strategy registered in `trex_tvr.strategies.registry`: the
strategy and its params come from the registry + config `[strategy.<id>]` table
(the same path the live daemon uses), so the backtest never hard-codes a strategy.

Generic backtest/exit knobs are CLI flags; strategy-specific params live in the
config. Precedence for the generic knobs: explicit flag > config > default.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date


def _parse_date_range(value: str) -> list[str]:
    """Parse YYYYMMDD:YYYYMMDD (or YYYY-MM-DD:...) into a list of YYYYMMDD strings."""
    parts = value.split(":")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"date-range must be start:end, got {value!r}")
    start_s, end_s = (p.replace("-", "") for p in parts)
    try:
        start = date(int(start_s[:4]), int(start_s[4:6]), int(start_s[6:8]))
        end = date(int(end_s[:4]), int(end_s[4:6]), int(end_s[6:8]))
    except (ValueError, IndexError) as exc:
        raise argparse.ArgumentTypeError(f"invalid date in range: {exc}") from exc
    from datetime import timedelta

    days, current = [], start
    while current <= end:
        days.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return days


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="trex-tvr-backtest",
        description="Strategy-agnostic backtest — local dump replay",
    )
    parser.add_argument("--universe", default="config/backtest_universe.csv")
    parser.add_argument("--dump-dir", default="data/dump")
    parser.add_argument("--date-range", required=True, type=_parse_date_range,
                        metavar="YYYYMMDD:YYYYMMDD")
    parser.add_argument("--config", default=None,
                        help="strategy config TOML — a name ('test') or a path. Loads "
                             "[strategy.<id>] + [exit] + entry_capital_krw.")
    parser.add_argument("--config-path", default=None,
                        help="config directory for --config name resolution (default ./config)")
    parser.add_argument("--strategy", default=None,
                        help="strategy_id to backtest (default: from --config, else the "
                             "single registered strategy)")
    parser.add_argument("--output", default=None, help="output CSV path for trade records")
    parser.add_argument("--capacity", type=int, default=20,
                        help="max simultaneous positions per day (default 20)")
    parser.add_argument("--entry-capital", type=int, default=None,
                        help="per-position capital in KRW (default 10_000_000)")
    parser.add_argument("--stop-loss-pct", type=float, default=None,
                        help="exit stop-loss %% (default 2.0; overrides [exit].stop_loss_pct)")
    parser.add_argument("--take-profit-pct", type=float, default=None,
                        help="exit take-profit %% (default 4.0; overrides [exit].take_profit_pct)")
    parser.add_argument("--fee-pct", type=float, default=0.015)
    parser.add_argument("--slippage-pct", type=float, default=0.05)
    parser.add_argument("--sell-tax-pct", type=float, default=0.15,
                        help="KR securities transaction tax, sell side only (default 0.15)")
    parser.add_argument("--entry-cutoff", default=None,
                        metavar="HH:MM", help="KST entry cutoff (default 10:30)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    from trex_tvr.backtest.bt_config import load_backtest_config, pick, resolve_config_arg
    from trex_tvr.backtest.dump_loader import DumpLoader
    from trex_tvr.backtest.exit_simulator import ExitSimulator
    from trex_tvr.backtest.metrics import format_text_report, write_csv_report
    from trex_tvr.backtest.replay_engine import BacktestEngine
    from trex_tvr.backtest.universe import filter_by_date, load_universe
    from trex_tvr.strategies import registry

    cfg_strategy: dict = {}
    cfg_exit: dict = {}
    cfg_strategy_id = None
    cfg_entry_capital = None
    if args.config:
        config_file = resolve_config_arg(args.config, args.config_path)
        try:
            cfg_strategy_id, cfg_strategy, cfg_exit, cfg_entry_capital = load_backtest_config(
                config_file
            )
        except (OSError, ValueError) as exc:
            parser.error(f"--config load failed for {config_file!r}: {exc}")

    registry.ensure_default_strategies_loaded()
    strategy_id = args.strategy or cfg_strategy_id
    if strategy_id is None:
        ids = registry.registered_ids()
        if len(ids) == 1:
            strategy_id = ids[0]
        else:
            parser.error(f"specify --strategy (registered: {ids})")
    if not registry.is_registered(strategy_id):
        parser.error(f"strategy_id {strategy_id!r} not registered: {registry.registered_ids()}")

    # Strategy built via the registry from config params (same path as the live daemon).
    strategy = registry.instantiate_strategy(strategy_id, cfg_strategy)

    stop_loss_pct = pick(args.stop_loss_pct, cfg_exit, "stop_loss_pct", 2.0)
    take_profit_pct = pick(args.take_profit_pct, cfg_exit, "take_profit_pct", 4.0)
    entry_capital = pick(args.entry_capital, {}, "", None)
    if entry_capital is None:
        entry_capital = cfg_entry_capital if cfg_entry_capital is not None else 10_000_000
    entry_cutoff_str = pick(args.entry_cutoff, cfg_strategy, "entry_cutoff", "10:30")

    from datetime import time as time_cls
    try:
        hh, mm = entry_cutoff_str.split(":")
        entry_cutoff = time_cls(int(hh), int(mm))
    except (ValueError, AttributeError) as exc:
        parser.error(f"entry-cutoff must be HH:MM, got {entry_cutoff_str!r}: {exc}")

    exit_simulator = ExitSimulator(
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        fee_pct=args.fee_pct,
        slippage_pct=args.slippage_pct,
        sell_tax_pct=args.sell_tax_pct,
    )
    engine = BacktestEngine(
        strategy=strategy,
        dump_loader=DumpLoader(args.dump_dir),
        exit_simulator=exit_simulator,
        capacity=args.capacity,
        entry_cutoff=entry_cutoff,
    )

    universe_entries = load_universe(args.universe)
    date_range: list[str] = args.date_range
    from datetime import date as date_cls

    all_symbols: list[str] = []
    for d_str in date_range:
        d = date_cls(int(d_str[:4]), int(d_str[4:6]), int(d_str[6:8]))
        for code in filter_by_date(universe_entries, d):
            if code not in all_symbols:
                all_symbols.append(code)

    logger.info("backtest: strategy=%s universe=%d symbols, dates=%d",
                strategy_id, len(all_symbols), len(date_range))

    result = engine.run_multi_day(
        all_symbols,
        date_range,
        asset_template={"entry_capital": entry_capital, "allocated_capital": entry_capital},
    )
    print(format_text_report(result.trades, result.metrics))

    if args.output:
        write_csv_report(result.trades, args.output)
        logger.info("trade records written to %s", args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
