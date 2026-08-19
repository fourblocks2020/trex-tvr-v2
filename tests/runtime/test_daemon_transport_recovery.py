from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from tests.conftest import KST, StubEventsPort, StubMarketData, holding_slot, inactive_slot
from trex_tvr.runtime.config import Config, KisDataHubConfig, ScheduleEntry
from trex_tvr.runtime.daemon import Daemon
from trex_tvr.transport.base import RpcTimeoutError


_TVR_PARAMS = {
    "target_weight": 0.30,
    "rebalance_band_pct": 5.0,
    "min_trade_krw": 10_000,
}


def _config() -> Config:
    return Config(
        instance_key="default",
        rabbitmq_url="amqp://localhost",
        kis_data_hub=KisDataHubConfig(base_url="http://kdh.test"),
        schedule=[ScheduleEntry(start="08:00", end="20:00")],
        strategy_id="tvr_v1",
        strategy_params={"tvr_v1": {}},
        redis_url="redis://localhost",
        poll_interval=60,
    )


class SequencedSlotPort:
    def __init__(self, responses: list[dict], trace: list[str]) -> None:
        self.responses = iter(responses)
        self.trace = trace
        self.calls = 0

    def request_slots(self, *, filter: str, strategy: str | None = None) -> dict:
        self.calls += 1
        response = next(self.responses)
        status = response["slots"][0]["status"]
        self.trace.append(f"slot:{status}")
        return response


class ReplyLostIntentPort:
    """Models core admission followed by a lost RPC reply."""

    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.buy_payloads: list[dict] = []
        self.sell_payloads: list[dict] = []

    def submit_buy(self, payload: dict) -> dict:
        self.buy_payloads.append(payload)
        self.trace.append("intent:buy:reply_lost")
        raise RpcTimeoutError("core admission may have completed; reply lost")

    def submit_sell(self, payload: dict) -> dict:
        self.sell_payloads.append(payload)
        self.trace.append("intent:sell:reply_lost")
        raise RpcTimeoutError("core admission may have completed; reply lost")


@pytest.mark.parametrize(
    ("side", "first_slot", "converged_status", "price"),
    [
        (
            "buy",
            inactive_slot(qty=0, cash_balance=100_000, strategy_params=_TVR_PARAMS),
            "pending_buy",
            5_000,
        ),
        (
            "sell",
            holding_slot(qty=20, cash_balance=100_000, strategy_params=_TVR_PARAMS),
            "pending_sell",
            5_000,
        ),
    ],
)
def test_ambiguous_intent_reply_loss_reconnects_and_reads_snapshot_before_new_intent(
    tmp_path, side, first_slot, converged_status, price
):
    trace: list[str] = []
    converged_slot = {**first_slot, "status": converged_status}
    slots = SequencedSlotPort(
        [
            {"slots": [first_slot], "degraded": False},
            {"slots": [converged_slot], "degraded": False},
        ],
        trace,
    )
    intents = ReplyLostIntentPort(trace)

    def reconnect() -> None:
        trace.append("reconnect")

    daemon = Daemon(
        _config(),
        slot_port=slots,
        intent_port=intents,
        events_port=StubEventsPort(),
        market_data_client=StubMarketData(price),
        clock=lambda: datetime(2026, 6, 8, 10, 0, tzinfo=KST),
        reconnect_fn=reconnect,
        idle_wait_fn=lambda _seconds: None,
        status_path=str(tmp_path / "status.json"),
    )

    with patch("trex_tvr.runtime.daemon.time.sleep"):
        daemon.run_forever(_max_cycles=1)

    assert trace == [
        f"slot:{first_slot['status']}",
        f"intent:{side}:reply_lost",
        "reconnect",
        f"slot:{converged_status}",
    ]
    assert slots.calls == 2
    assert len(intents.buy_payloads) + len(intents.sell_payloads) == 1
    assert len(intents.buy_payloads if side == "buy" else intents.sell_payloads) == 1
    assert daemon._snapshot.deps.rabbitmq == "ok"
    assert daemon._snapshot.last_error is None
