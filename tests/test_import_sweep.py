"""Architecture seam guard via import sweep — the heart of the template.

- trex_tvr/strategies/ must not import transport/market_data/redis/pika/httpx.
- trex_tvr (whole package) must not import trex_core (pure AMQP).
- The checker is self-tested so a forbidden import would actually fail.

This is what keeps the strategy layer pluggable: keep your strategy infra-free.
"""
from __future__ import annotations

import ast
import pathlib

_PKG_ROOT = pathlib.Path(__file__).resolve().parents[1] / "trex_tvr"

_STRATEGY_FORBIDDEN = (
    "httpx",
    "pika",
    "redis",
    "trex_tvr.transport",
    "trex_tvr.market_data",
)
_GLOBAL_FORBIDDEN = ("trex_core",)


def _imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                modules.add(node.module)
    return modules


def _violations(source: str, forbidden: tuple[str, ...]) -> set[str]:
    hits: set[str] = set()
    for module in _imported_modules(source):
        for bad in forbidden:
            if module == bad or module.startswith(bad + "."):
                hits.add(module)
    return hits


def _py_files(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(root.rglob("*.py"))


def test_strategies_have_no_infra_imports():
    offenders: dict[str, set[str]] = {}
    for path in _py_files(_PKG_ROOT / "strategies"):
        hits = _violations(path.read_text(), _STRATEGY_FORBIDDEN)
        if hits:
            offenders[str(path.relative_to(_PKG_ROOT))] = hits
    assert offenders == {}, f"strategies/ must stay infra-free, found: {offenders}"


def test_no_trex_core_import_anywhere():
    offenders: dict[str, set[str]] = {}
    for path in _py_files(_PKG_ROOT):
        hits = _violations(path.read_text(), _GLOBAL_FORBIDDEN)
        if hits:
            offenders[str(path.relative_to(_PKG_ROOT))] = hits
    assert offenders == {}, f"trex_core import is forbidden (pure AMQP), found: {offenders}"


def test_guard_detects_a_forbidden_import():
    assert _violations("import httpx", _STRATEGY_FORBIDDEN) == {"httpx"}
    assert _violations("from trex_tvr.transport.base import X", _STRATEGY_FORBIDDEN) == {
        "trex_tvr.transport.base"
    }
    assert _violations("import trex_core.foo", _GLOBAL_FORBIDDEN) == {"trex_core.foo"}
    assert _violations("from trex_tvr.strategies.base import BuyStrategy", _STRATEGY_FORBIDDEN) == set()
