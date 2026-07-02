"""console_script entry point.

    trex-tvr --config <name> [--config-path <dir>] [--env-file <path>]

`--config <name>` selects a per-instance TOML inside the config directory
(`--config-path`, default `./config`); `.toml` is appended if absent. The
matching `.env` is `<config_dir>/.env` unless `--env-file` overrides it. Real
exported env vars always win over the `.env` file.
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from trex_tvr.runtime.config import ConfigError, resolve_config_toml


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trex-tvr", description="trex-core buy-strategy entry daemon (TVR — Target-Value Rebalancer)."
    )
    parser.add_argument(
        "--config",
        required=True,
        metavar="NAME",
        help="instance config name inside the config dir (e.g. 'test' -> <config_dir>/test.toml)",
    )
    parser.add_argument(
        "--config-path",
        metavar="DIR",
        default=None,
        help="config directory holding the TOML + .env (default: ./config)",
    )
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        default=None,
        help="explicit .env path (default: <config_dir>/.env; real env vars still win)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="log startup gate (RabbitMQ/kis health) and per-cycle status to stderr",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config_toml = resolve_config_toml(args.config, args.config_path)
    # Imported lazily so `--help` / arg parsing works without runtime deps wired.
    from trex_tvr.runtime.daemon import run_from_config

    try:
        return run_from_config(config_toml, env_file=args.env_file, verbose=args.verbose)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
