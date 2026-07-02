"""Backtest config loading + resolution (strategy-agnostic)."""
from __future__ import annotations

from pathlib import Path

from trex_tvr.backtest.bt_config import load_backtest_config, pick, resolve_config_arg


def _write(tmp_path: Path, body: str) -> str:
    p = tmp_path / "cfg.toml"
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_load_extracts_strategy_exit_and_capital(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        entry_capital_krw = 3000000
        [strategy.tvr_v1]
        min_price = 5000
        [exit]
        stop_loss_pct = 2.5
        take_profit_pct = 4.0
        """,
    )
    sid, strat, exit_, cap = load_backtest_config(path)
    assert sid == "tvr_v1"
    assert strat["min_price"] == 5000  # arbitrary strategy param passed through verbatim
    assert exit_["stop_loss_pct"] == 2.5 and exit_["take_profit_pct"] == 4.0
    assert cap == 3_000_000


def test_load_ignores_runtime_sections_and_missing_tables(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        instance_key = "test"
        [[schedule]]
        start = "09:15"
        end = "10:30"
        [strategy.tvr_v1]
        min_price = 0
        """,
    )
    sid, strat, exit_, cap = load_backtest_config(path)
    assert sid == "tvr_v1" and strat["min_price"] == 0
    assert exit_ == {} and cap is None  # no [exit]/capital -> empty/None, no error


def test_pick_precedence() -> None:
    # flag > config > default
    assert pick(5.0, {"k": 2.0}, "k", 1.0) == 5.0
    assert pick(None, {"k": 2.0}, "k", 1.0) == 2.0
    assert pick(None, {}, "k", 1.0) == 1.0


def test_resolve_config_arg_name_and_path(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "test.toml").write_text("instance_key = 'x'\n", encoding="utf-8")
    # bare name -> <config_path>/<name>.toml
    assert resolve_config_arg("test", str(cfg_dir)) == str(cfg_dir / "test.toml")
    # existing path -> used as-is
    p = tmp_path / "my.toml"
    p.write_text("instance_key = 'x'\n", encoding="utf-8")
    assert resolve_config_arg(str(p), str(cfg_dir)) == str(p)
