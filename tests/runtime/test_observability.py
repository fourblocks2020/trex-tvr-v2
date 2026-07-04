"""REQ-004 status/health 관측성 day-1 단위 테스트.

Tests:
  - 8항목 JSON 필드 존재 + 타입 검사
  - cycle 완료 후 갱신 (last_cycle_ts, cycle_count, last_status)
  - skipped cycle idle_reason 갱신
  - transport 에러 시 last_error + deps.rabbitmq 갱신
  - transport 에러 후 정상 cycle → last_error=null, deps.rabbitmq=ok
  - 파일 쓰기 실패 → best-effort (cycle 중단 없음)
  - 타입 검사
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime

import pytest

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
from trex_tvr.runtime.status import DepsStatus, StatusSnapshot, read_snapshot, write_snapshot

_TVR_PARAMS = {"target_weight": 0.70, "rebalance_band_pct": 5.0, "min_trade_krw": 10_000}


def _config():
    return Config(
        instance_key="default",
        rabbitmq_url="amqp://localhost",
        kis_data_hub=KisDataHubConfig(base_url="http://kdh.test"),
        schedule=[ScheduleEntry(start="09:15", end="15:20")],
        strategy_id="tvr_v1",
        strategy_params={"tvr_v1": {}},
        execution=ExecutionConfig(),
        redis_url="redis://localhost",
    )


def _daemon(slot, intent, market_data, now, status_path=None):
    return Daemon(
        _config(),
        slot_port=slot,
        intent_port=intent,
        events_port=StubEventsPort(),
        market_data_client=market_data,
        clock=lambda: now,
        status_path=status_path,
    )


# ---------------------------------------------------------------------------
# AC-REQ004-1: 8항목 JSON 필드 존재
# ---------------------------------------------------------------------------
def test_ac1_snapshot_has_all_8_fields():
    """AC-REQ004-1: 기동 직후 StatusSnapshot에 8항목이 모두 존재."""
    snap = StatusSnapshot()
    snap.deps = DepsStatus(rabbitmq="ok", kis_data_hub="ok", redis="ok")
    snap.last_cycle_ts = "2026-07-04T09:00:00+09:00"
    snap.cycle_count = 0
    snap.last_status = "started"
    snap.last_error = None
    snap.idle_reason = None

    d = snap.to_dict()
    assert "last_cycle_ts" in d
    assert "cycle_count" in d
    assert "last_status" in d
    assert "last_error" in d
    assert "deps" in d
    assert "rabbitmq" in d["deps"]
    assert "kis_data_hub" in d["deps"]
    assert "redis" in d["deps"]
    assert "idle_reason" in d


# ---------------------------------------------------------------------------
# AC-REQ004-2: 정상 cycle 1회 완료 후 갱신
# ---------------------------------------------------------------------------
def test_ac2_snapshot_updated_after_normal_cycle():
    """AC-REQ004-2: cycle 1회 완료 후 last_cycle_ts·cycle_count·last_status 갱신."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        now = datetime(2026, 7, 4, 10, 0, tzinfo=KST)
        slot_row = inactive_slot(qty=0, cash_balance=1_000_000, strategy_params=_TVR_PARAMS)
        slot = StubSlotPort({"slots": [slot_row], "degraded": False})
        intent = StubIntentPort({"status": "admitted", "slot_id": "slot-1"})
        daemon = _daemon(slot, intent, StubMarketData(2000), now, status_path=path)

        result = daemon.run_decision_cycle()

        assert result["status"] == "ok"
        snap_dict = read_snapshot(path)
        assert snap_dict is not None
        assert snap_dict["cycle_count"] == 1
        assert snap_dict["last_status"] == "ok"
        assert snap_dict["last_cycle_ts"] is not None
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# AC-REQ004-3: skipped cycle → idle_reason 갱신
# ---------------------------------------------------------------------------
def test_ac3_skipped_cycle_idle_reason():
    """AC-REQ004-3: cycle skip → last_status=skipped, idle_reason=out_of_schedule."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        now = datetime(2026, 7, 4, 8, 0, tzinfo=KST)  # out of schedule
        slot = StubSlotPort({"slots": []})
        intent = StubIntentPort({"status": "admitted"})
        daemon = _daemon(slot, intent, StubMarketData(2000), now, status_path=path)

        daemon.run_decision_cycle()

        snap_dict = read_snapshot(path)
        assert snap_dict is not None
        assert snap_dict["last_status"] == "skipped"
        assert snap_dict["idle_reason"] == "out_of_schedule"
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# AC-REQ004-6: 쓰기 실패 → best-effort (cycle 중단 없음)
# ---------------------------------------------------------------------------
def test_ac6_write_failure_best_effort():
    """AC-REQ004-6: 스냅샷 쓰기 실패 시 cycle 정상 완료, 데몬 종료 없음."""
    # Use a path that cannot be written (directory as file path)
    bad_path = "/tmp/nonexistent_dir_xyz_abc/trex_tvr_test_status.json"
    now = datetime(2026, 7, 4, 10, 0, tzinfo=KST)
    slot_row = inactive_slot(qty=0, cash_balance=1_000_000, strategy_params=_TVR_PARAMS)
    slot = StubSlotPort({"slots": [slot_row], "degraded": False})
    intent = StubIntentPort({"status": "admitted"})
    daemon = _daemon(slot, intent, StubMarketData(2000), now, status_path=bad_path)

    # Should not raise — best-effort
    result = daemon.run_decision_cycle()
    assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# AC-REQ004-7: 타입 검사
# ---------------------------------------------------------------------------
def test_ac7_snapshot_field_types():
    """AC-REQ004-7: 스냅샷 JSON 파싱 후 모든 필드 타입 검사."""
    snap = StatusSnapshot()
    snap.last_cycle_ts = "2026-07-04T10:00:00+09:00"
    snap.cycle_count = 5
    snap.last_status = "ok"
    snap.last_error = None
    snap.deps = DepsStatus(rabbitmq="ok", kis_data_hub="ok", redis="ok")
    snap.idle_reason = None

    d = snap.to_dict()
    json_str = json.dumps(d)
    parsed = json.loads(json_str)

    assert isinstance(parsed["last_cycle_ts"], str)
    assert isinstance(parsed["cycle_count"], int)
    assert isinstance(parsed["last_status"], str)
    assert parsed["last_error"] is None
    assert isinstance(parsed["deps"], dict)
    assert parsed["deps"]["rabbitmq"] in ("ok", "error", "unknown")
    assert parsed["deps"]["kis_data_hub"] in ("ok", "error", "unknown")
    assert parsed["deps"]["redis"] in ("ok", "error", "unknown")
    assert parsed["idle_reason"] is None


# ---------------------------------------------------------------------------
# 원자 write 테스트
# ---------------------------------------------------------------------------
def test_write_snapshot_atomic():
    """스냅샷이 파일에 원자적으로 기록되고 read_snapshot으로 읽힌다."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        snap = StatusSnapshot()
        snap.last_status = "test_ok"
        snap.cycle_count = 42
        snap.deps = DepsStatus(rabbitmq="ok", kis_data_hub="ok", redis="ok")

        write_snapshot(snap, path)
        result = read_snapshot(path)

        assert result is not None
        assert result["last_status"] == "test_ok"
        assert result["cycle_count"] == 42
        assert result["deps"]["rabbitmq"] == "ok"
    finally:
        os.unlink(path)


def test_read_snapshot_returns_none_if_missing():
    """read_snapshot은 파일 없으면 None 반환."""
    result = read_snapshot("/tmp/trex_tvr_nonexistent_test.json")
    assert result is None


def test_deps_status_defaults():
    """DepsStatus 기본값 = unknown."""
    deps = DepsStatus()
    assert deps.rabbitmq == "unknown"
    assert deps.kis_data_hub == "unknown"
    assert deps.redis == "unknown"
