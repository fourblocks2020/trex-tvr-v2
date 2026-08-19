"""REQ-006: 케이던스 — 60s 바닥 + 무슬립 드레인 단위 테스트.

monotonic을 mock해 elapsed를 주입하고 sleep 호출 인자를 검증하는 방식으로
실시간 슬립 없이 단위 테스트. AC-4(실시간 타이밍)는 monotonic mock으로 대체.

AC 매핑:
  AC-1: elapsed=10s, poll_interval=60 → sleep(≈50)
  AC-2: elapsed=65s, poll_interval=60 → no sleep (drain)
  AC-3: elapsed=60s, poll_interval=60 → sleep(0) or no sleep (boundary)
  AC-4: poll_interval=5, stub cycle, 3회 → 각 cycle 간격 ≈5s (monotonic mock)
  AC-5: 고정 슬립 코드 없음 (코드 리뷰)
  AC-6: instance.example.toml에 poll_interval 존재
  AC-7: session_hold도 60s 바닥 적용
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from tests.conftest import (
    KST,
    StubEventsPort,
    StubIntentPort,
    StubMarketData,
    StubSlotPort,
    inactive_slot,
)
from trex_tvr.runtime.config import Config, ExecutionConfig, KisDataHubConfig, ScheduleEntry
from trex_tvr.runtime.daemon import Daemon

_TVR_PARAMS = {"target_weight": 0.70, "rebalance_band_pct": 5.0, "min_trade_krw": 10000}


def _config(poll_interval: int = 60) -> Config:
    return Config(
        instance_key="default",
        rabbitmq_url="amqp://localhost",
        kis_data_hub=KisDataHubConfig(base_url="http://kdh.test"),
        schedule=[ScheduleEntry(start="08:00", end="20:00")],
        strategy_id="tvr_v1",
        strategy_params={"tvr_v1": {}},
        execution=ExecutionConfig(),
        redis_url="redis://localhost",
        poll_interval=poll_interval,
    )


def _daemon_with_slot(slot_resp, market_data, now):
    slot = StubSlotPort(slot_resp)
    intent = StubIntentPort({"status": "admitted", "slot_id": "slot-1"})
    return Daemon(
        _config(),
        slot_port=slot,
        intent_port=intent,
        events_port=StubEventsPort(),
        market_data_client=market_data,
        clock=lambda: now,
    )


# ---------------------------------------------------------------------------
# AC-1: elapsed=10s, poll_interval=60 → sleep(≈50)
# ---------------------------------------------------------------------------
def test_ac1_floor_sleep_when_cycle_fast():
    """AC-1: cycle 소요 10s, poll_interval=60 → sleep(≈50s) 호출."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)
    slot_row = inactive_slot(qty=0, cash_balance=1_000_000, strategy_params=_TVR_PARAMS)
    slot = StubSlotPort({"slots": [slot_row], "degraded": False})
    intent = StubIntentPort({"status": "admitted", "slot_id": "slot-1"})
    daemon = Daemon(
        _config(poll_interval=60),
        slot_port=slot,
        intent_port=intent,
        events_port=StubEventsPort(),
        market_data_client=StubMarketData(2000),
        clock=lambda: now,
    )

    # monotonic calls in order per cycle (run_forever + run_decision_cycle):
    # 1. run_forever: t_start = monotonic() → 1000.0
    # 2. run_decision_cycle: cycle_start = monotonic() → 1000.0
    # 3. run_decision_cycle: cycle_elapsed = monotonic() - cycle_start → 1010.0 (elapsed=10s)
    # 4. run_forever: elapsed = monotonic() - t_start → 1010.0 (elapsed=10s, remaining=50s)
    mono_values = iter([1000.0, 1000.0, 1010.0, 1010.0])

    slept = []
    with patch("trex_tvr.runtime.daemon.time.monotonic", side_effect=lambda: next(mono_values)):
        with patch("trex_tvr.runtime.daemon.time.sleep", side_effect=lambda s: slept.append(s)):
            daemon.run_forever(_max_cycles=1)

    assert len(slept) == 1
    assert abs(slept[0] - 50.0) < 0.01, f"Expected sleep(≈50), got sleep({slept[0]})"


def test_issue6_idle_wait_pumps_rabbitmq_heartbeats_instead_of_sleeping():
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)
    slot_row = inactive_slot(qty=0, cash_balance=1_000_000, strategy_params=_TVR_PARAMS)
    idle_waits = []
    daemon = Daemon(
        _config(poll_interval=60),
        slot_port=StubSlotPort({"slots": [slot_row], "degraded": False}),
        intent_port=StubIntentPort({"status": "admitted", "slot_id": "slot-1"}),
        events_port=StubEventsPort(),
        market_data_client=StubMarketData(2000),
        clock=lambda: now,
        idle_wait_fn=lambda seconds: idle_waits.append(seconds),
    )
    mono_values = iter([1000.0, 1000.0, 1010.0, 1010.0])

    with patch("trex_tvr.runtime.daemon.time.monotonic", side_effect=lambda: next(mono_values)):
        with patch("trex_tvr.runtime.daemon.time.sleep") as sleep:
            daemon.run_forever(_max_cycles=1)

    assert idle_waits == [50.0]
    sleep.assert_not_called()


# ---------------------------------------------------------------------------
# AC-2: elapsed=65s, poll_interval=60 → no sleep (drain)
# ---------------------------------------------------------------------------
def test_ac2_no_sleep_when_cycle_overruns():
    """AC-2: cycle 소요 65s, poll_interval=60 → sleep 미호출 (무슬립 드레인)."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)
    slot_row = inactive_slot(qty=0, cash_balance=1_000_000, strategy_params=_TVR_PARAMS)
    slot = StubSlotPort({"slots": [slot_row], "degraded": False})
    intent = StubIntentPort({"status": "admitted", "slot_id": "slot-1"})
    daemon = Daemon(
        _config(poll_interval=60),
        slot_port=slot,
        intent_port=intent,
        events_port=StubEventsPort(),
        market_data_client=StubMarketData(2000),
        clock=lambda: now,
    )

    # elapsed=65s → remaining=-5s → no sleep
    # monotonic calls: t_start=1000, cycle_start=1000, cycle_end=1065, elapsed_check=1065
    mono_values = iter([1000.0, 1000.0, 1065.0, 1065.0])

    slept = []
    with patch("trex_tvr.runtime.daemon.time.monotonic", side_effect=lambda: next(mono_values)):
        with patch("trex_tvr.runtime.daemon.time.sleep", side_effect=lambda s: slept.append(s)):
            daemon.run_forever(_max_cycles=1)

    # No sleep should be called for cycle draining (remaining <= 0).
    assert all(s <= 0 for s in slept) or len(slept) == 0, (
        f"Expected no sleep or sleep(<=0) for drain, got {slept}"
    )


# ---------------------------------------------------------------------------
# AC-3: elapsed=60s (boundary) → sleep(0) or no sleep
# ---------------------------------------------------------------------------
def test_ac3_boundary_elapsed_equals_poll_interval():
    """AC-3: elapsed=60s, poll_interval=60 → sleep(0) or immediate next cycle."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)
    slot_row = inactive_slot(qty=0, cash_balance=1_000_000, strategy_params=_TVR_PARAMS)
    slot = StubSlotPort({"slots": [slot_row], "degraded": False})
    intent = StubIntentPort({"status": "admitted", "slot_id": "slot-1"})
    daemon = Daemon(
        _config(poll_interval=60),
        slot_port=slot,
        intent_port=intent,
        events_port=StubEventsPort(),
        market_data_client=StubMarketData(2000),
        clock=lambda: now,
    )

    # elapsed=60s → remaining=0 → sleep(0) is acceptable, or no sleep
    # monotonic calls: t_start=1000, cycle_start=1000, cycle_end=1060, elapsed_check=1060
    mono_values = iter([1000.0, 1000.0, 1060.0, 1060.0])

    slept = []
    with patch("trex_tvr.runtime.daemon.time.monotonic", side_effect=lambda: next(mono_values)):
        with patch("trex_tvr.runtime.daemon.time.sleep", side_effect=lambda s: slept.append(s)):
            daemon.run_forever(_max_cycles=1)

    # Boundary: remaining=0, so sleep(0) or no sleep — both are OK.
    assert all(s <= 0.001 for s in slept), (
        f"Expected sleep(0) or no sleep at boundary, got {slept}"
    )


# ---------------------------------------------------------------------------
# AC-4: poll_interval=5, stub cycle, multiple cycles → monotonic mock validation
# ---------------------------------------------------------------------------
def test_ac4_multiple_cycles_monotonic_mock():
    """AC-4: poll_interval=5, 3 cycles, each instant → sleep(≈5) per cycle."""
    now = datetime(2026, 6, 8, 10, 0, tzinfo=KST)
    slot = StubSlotPort({"slots": [], "degraded": False})
    intent = StubIntentPort({"status": "admitted"})
    daemon = Daemon(
        _config(poll_interval=5),
        slot_port=slot,
        intent_port=intent,
        events_port=StubEventsPort(),
        market_data_client=StubMarketData(2000),
        clock=lambda: now,
    )

    # 3 cycles: each cycle uses empty slots → no_op (skips cycle_start/cycle_end in Phase1).
    # Monotonic calls per cycle:
    #   1. run_forever: t_start = monotonic() → 0.0
    #   2. run_forever: elapsed = monotonic() - t_start → 0.1  (elapsed=0.1)
    # So per no_op cycle: 2 monotonic calls, sleep(5 - 0.1) ≈ 4.9s
    mono_sequence = [0.0, 0.1,  # cycle 1
                     0.0, 0.1,  # cycle 2
                     0.0, 0.1]  # cycle 3
    mono_iter = iter(mono_sequence)

    slept = []
    with patch("trex_tvr.runtime.daemon.time.monotonic", side_effect=lambda: next(mono_iter)):
        with patch("trex_tvr.runtime.daemon.time.sleep", side_effect=lambda s: slept.append(s)):
            daemon.run_forever(_max_cycles=3)

    # Each cycle should sleep ≈5s (5 - 0.1 = 4.9s)
    assert len(slept) == 3, f"Expected 3 sleep calls (one per cycle), got {len(slept)}"
    for s in slept:
        assert 4.8 <= s <= 5.1, f"Expected sleep≈5s, got {s}"


# ---------------------------------------------------------------------------
# AC-5: no fixed sleep code in daemon.py
# ---------------------------------------------------------------------------
def test_ac5_no_fixed_sleep_in_daemon():
    """AC-5: daemon.py には고정 슬립(sleep(30) 등) 코드가 없음."""
    import ast
    import pathlib
    src = pathlib.Path(__file__).parent.parent.parent / "trex_tvr" / "runtime" / "daemon.py"
    tree = ast.parse(src.read_text())

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Check for time.sleep(<literal>)
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "sleep"):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)):
                        val = arg.value
                        # Fixed constant sleeps (e.g., sleep(30)) are forbidden.
                        # Variable-based sleeps (sleep(remaining), sleep(delay)) are allowed.
                        assert val == 0 or val > 60, (
                            f"Fixed sleep({val}) found in daemon.py — should be variable-based drain/floor"
                        )


# ---------------------------------------------------------------------------
# AC-6: instance.example.toml has poll_interval key
# ---------------------------------------------------------------------------
def test_ac6_instance_example_toml_has_poll_interval():
    """AC-6: config/instance.example.toml contains poll_interval key with unit hint."""
    import pathlib
    toml_path = pathlib.Path(__file__).parent.parent.parent / "config" / "instance.example.toml"
    content = toml_path.read_text()
    assert "poll_interval" in content, "instance.example.toml must contain 'poll_interval'"
    # Should have seconds unit mentioned
    assert "second" in content.lower() or "# seconds" in content.lower() or "60" in content, (
        "poll_interval should have a unit hint (seconds) in the comment"
    )


# ---------------------------------------------------------------------------
# AC-7: session_hold cycle also gets 60s floor applied
# ---------------------------------------------------------------------------
def test_ac7_session_hold_gets_floor_sleep():
    """AC-7: session_hold cycle body returns immediately → still gets poll_interval floor sleep."""
    # Use a time outside the schedule (before 08:00) to trigger out_of_schedule/session_hold.
    now = datetime(2026, 6, 8, 7, 0, tzinfo=KST)  # before session
    slot = StubSlotPort({"slots": [], "degraded": False})
    intent = StubIntentPort({"status": "admitted"})
    daemon = Daemon(
        _config(poll_interval=60),
        slot_port=slot,
        intent_port=intent,
        events_port=StubEventsPort(),
        market_data_client=StubMarketData(2000),
        clock=lambda: now,
    )

    # Cycle body returns instantly (out_of_schedule at 07:00) → elapsed=0.05 → remaining≈60.
    # For skipped/session_hold cycles (no Phase 1), monotonic calls:
    #   1. run_forever: t_start = monotonic() → 1000.0
    #   2. run_forever: elapsed = monotonic() - t_start → 1000.05 (elapsed=0.05)
    mono_values = iter([1000.0, 1000.05])

    slept = []
    with patch("trex_tvr.runtime.daemon.time.monotonic", side_effect=lambda: next(mono_values)):
        with patch("trex_tvr.runtime.daemon.time.sleep", side_effect=lambda s: slept.append(s)):
            daemon.run_forever(_max_cycles=1)

    assert len(slept) == 1, f"Expected 1 sleep call for session_hold cycle, got {len(slept)}"
    assert slept[0] >= 59.0, f"Expected ≈60s sleep for session_hold, got {slept[0]}"


# ---------------------------------------------------------------------------
# Config: poll_interval parsing
# ---------------------------------------------------------------------------
def test_config_poll_interval_default():
    """Config default poll_interval is 60."""
    cfg = _config()
    assert cfg.poll_interval == 60


def test_config_poll_interval_custom():
    """Config accepts custom poll_interval."""
    cfg = _config(poll_interval=30)
    assert cfg.poll_interval == 30
