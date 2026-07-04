"""TVR daemon status/health snapshot — REQ-004 day-1 observability.

설계 결정 (BAT-001 design.md):
  - 수단: 파일 원자 write (tmp → rename) + CLI `tvr status` 서브커맨드
  - 경로: 환경변수 TREX_TVR_STATUS_PATH 또는 기본값 /tmp/trex_tvr_status.json
  - 쓰기 실패는 best-effort (catch + log warning, daemon cycle 중단 없음)
  - 스냅샷은 JSON 8항목: last_cycle_ts, cycle_count, last_status, last_error,
    deps.rabbitmq, deps.kis_data_hub, deps.redis, idle_reason

갱신 시점:
  - 데몬 기동 직후: 초기 스냅샷 (deps = startup probe 결과)
  - 매 cycle 완료 후: last_cycle_ts, cycle_count, last_status, last_error(정상이면 null), idle_reason
  - transport 에러 발생/회복 시: last_error, deps.rabbitmq
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

_KST = timezone(timedelta(hours=9))
_DEFAULT_STATUS_PATH = "/tmp/trex_tvr_status.json"
_ENV_STATUS_PATH = "TREX_TVR_STATUS_PATH"

logger = logging.getLogger("trex_tvr")


def _now_kst_iso() -> str:
    return datetime.now(tz=_KST).isoformat()


@dataclass
class DepsStatus:
    rabbitmq: str = "unknown"       # ok | error | unknown
    kis_data_hub: str = "unknown"   # ok | error | unknown
    redis: str = "unknown"          # ok | error | unknown


@dataclass
class StatusSnapshot:
    """TVR 데몬 상태 스냅샷 — REQ-004의 8항목."""

    last_cycle_ts: Optional[str] = None          # ISO-8601 KST
    cycle_count: int = 0
    last_status: str = "unknown"
    last_error: Optional[str] = None
    deps: DepsStatus = field(default_factory=DepsStatus)
    idle_reason: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # deps를 중첩 dict에서 플랫 키로 노출 (JSON 8항목 명세 준수)
        d["deps"] = {
            "rabbitmq": self.deps.rabbitmq,
            "kis_data_hub": self.deps.kis_data_hub,
            "redis": self.deps.redis,
        }
        return d


def get_status_path() -> str:
    """환경변수 TREX_TVR_STATUS_PATH 또는 기본값 /tmp/trex_tvr_status.json."""
    return os.environ.get(_ENV_STATUS_PATH) or _DEFAULT_STATUS_PATH


def write_snapshot(snapshot: StatusSnapshot, path: Optional[str] = None) -> None:
    """스냅샷을 원자적으로 파일에 기록한다 (tmp → rename).

    쓰기 실패는 best-effort: 예외를 catch하고 WARNING 로그 후 계속 진행.
    daemon cycle을 중단시키지 않는다 (REQ-004 AC-6).
    """
    target = path or get_status_path()
    try:
        target_dir = os.path.dirname(target) or "."
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tmp", dir=target_dir, delete=False
        ) as fh:
            json.dump(snapshot.to_dict(), fh, ensure_ascii=False, indent=2)
            tmp_path = fh.name
        os.replace(tmp_path, target)
    except Exception as exc:  # noqa: BLE001 — best-effort, catch all
        logger.warning("status snapshot write failed (best-effort, continuing): %s", exc)
        try:
            os.unlink(tmp_path)  # type: ignore[possibly-undefined]
        except Exception:
            pass


def read_snapshot(path: Optional[str] = None) -> Optional[dict]:
    """스냅샷 파일을 읽어 dict를 반환. 없거나 파싱 실패 시 None."""
    target = path or get_status_path()
    try:
        with open(target) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
