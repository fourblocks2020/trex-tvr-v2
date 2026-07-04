"""console_script entry point.

    trex-tvr run --config <name> [--config-path <dir>] [--env-file <path>]
    trex-tvr status [--path <status_file>]

`run` starts the rebalancing daemon (default subcommand for backward compat).
`status` reads and prints the status snapshot written by the running daemon.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from trex_tvr.runtime.config import ConfigError, resolve_config_toml


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trex-tvr",
        description="TVR — Target-Value Rebalancer daemon (run | status).",
    )
    sub = parser.add_subparsers(dest="command")

    # --- run subcommand (daemon) ---
    run_p = sub.add_parser("run", help="Start the TVR rebalancing daemon.")
    run_p.add_argument(
        "--config",
        required=True,
        metavar="NAME",
        help="instance config name inside the config dir (e.g. 'test' -> <config_dir>/test.toml)",
    )
    run_p.add_argument(
        "--config-path",
        metavar="DIR",
        default=None,
        help="config directory holding the TOML + .env (default: ./config)",
    )
    run_p.add_argument(
        "--env-file",
        metavar="PATH",
        default=None,
        help="explicit .env path (default: <config_dir>/.env; real env vars still win)",
    )
    run_p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="log startup gate (RabbitMQ/kis health) and per-cycle status to stderr",
    )

    # --- status subcommand (REQ-004) ---
    status_p = sub.add_parser("status", help="Print the daemon status snapshot.")
    status_p.add_argument(
        "--path",
        metavar="FILE",
        default=None,
        help="path to status JSON file (default: TREX_TVR_STATUS_PATH env or /tmp/trex_tvr_status.json)",
    )

    # Legacy: support old-style `trex-tvr --config <name>` without subcommand
    parser.add_argument("--config", metavar="NAME", default=None)
    parser.add_argument("--config-path", metavar="DIR", default=None)
    parser.add_argument("--env-file", metavar="PATH", default=None)
    parser.add_argument("-v", "--verbose", action="store_true")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command

    # Legacy fallback: no subcommand given but --config was provided
    if command is None and args.config:
        command = "run"

    if command == "status":
        from trex_tvr.runtime.status import read_snapshot, get_status_path
        path = args.path if hasattr(args, "path") and args.path else get_status_path()
        snap = read_snapshot(path)
        if snap is None:
            print(f"No status snapshot found at: {path}", file=sys.stderr)
            return 1
        print(json.dumps(snap, indent=2, ensure_ascii=False))
        return 0

    if command == "run" or command is None:
        if not args.config:
            print("error: --config is required for 'run'", file=sys.stderr)
            return 2
        config_toml = resolve_config_toml(args.config, args.config_path)
        from trex_tvr.runtime.daemon import run_from_config
        try:
            return run_from_config(config_toml, env_file=args.env_file, verbose=args.verbose)
        except ConfigError as exc:
            print(f"config error: {exc}", file=sys.stderr)
            return 2

    print(f"Unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
