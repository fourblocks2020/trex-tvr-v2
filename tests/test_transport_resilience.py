from __future__ import annotations

from types import SimpleNamespace

import pika
import pytest
from pika.exceptions import NackError

from trex_tvr.runtime.config import ConfigError, _from_mapping
from trex_tvr.transport.base import TransportUnavailableError
from trex_tvr.transport.connection import PikaConnection


def _raw_config(**extra):
    raw = {
        "instance_key": "default",
        "rabbitmq_url": "amqp://guest:guest@localhost:5672/%2F",
        "kis_data_hub": {"base_url": "http://kdh.test"},
        "schedule": [{"start": "08:00", "end": "20:00"}],
        "strategy": {"tvr_v1": {}},
        "cache": {"redis_url": "redis://localhost:6379/0"},
    }
    raw.update(extra)
    return raw


def test_rabbitmq_connection_profile_defaults(monkeypatch):
    for name in ("TREX_RABBITMQ_URL", "TREX_REDIS_URL", "KIS_DATA_HUB_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    config = _from_mapping(_raw_config())

    assert config.rabbitmq_connection.heartbeat_seconds == 30
    assert config.rabbitmq_connection.blocked_connection_timeout_seconds == 30
    assert config.rabbitmq_connection.socket_timeout_seconds == 10


def test_rabbitmq_connection_profile_accepts_positive_overrides(monkeypatch):
    monkeypatch.delenv("TREX_RABBITMQ_URL", raising=False)
    config = _from_mapping(
        _raw_config(
            rabbitmq_connection={
                "heartbeat_seconds": 45,
                "blocked_connection_timeout_seconds": 40,
                "socket_timeout_seconds": 12,
            }
        )
    )

    assert config.rabbitmq_connection.heartbeat_seconds == 45
    assert config.rabbitmq_connection.blocked_connection_timeout_seconds == 40
    assert config.rabbitmq_connection.socket_timeout_seconds == 12


@pytest.mark.parametrize(
    "key", ["heartbeat", "blocked_connection_timeout", "socket_timeout"]
)
def test_rabbitmq_url_tuning_duplicates_fail_closed_without_url_disclosure(monkeypatch, key):
    monkeypatch.delenv("TREX_RABBITMQ_URL", raising=False)
    secret_url = f"amqp://user:super-secret@localhost/%2F?{key}=30"

    with pytest.raises(ConfigError) as exc_info:
        _from_mapping(_raw_config(rabbitmq_url=secret_url))

    message = str(exc_info.value)
    assert key in message
    assert "super-secret" not in message
    assert secret_url not in message


@pytest.mark.parametrize(
    "value", [0, -1, "invalid"]
)
def test_rabbitmq_connection_profile_rejects_invalid_values(monkeypatch, value):
    monkeypatch.delenv("TREX_RABBITMQ_URL", raising=False)

    with pytest.raises(ConfigError, match="heartbeat_seconds"):
        _from_mapping(
            _raw_config(rabbitmq_connection={"heartbeat_seconds": value})
        )


def test_connect_applies_typed_profile_and_enables_publisher_confirms(monkeypatch):
    captured = {}

    class FakeChannel:
        def confirm_delivery(self):
            captured["confirm_delivery"] = True

    class FakeBlockingConnection:
        is_open = True

        def __init__(self, parameters):
            captured["parameters"] = parameters
            self._channel = FakeChannel()

        def channel(self):
            return self._channel

        def close(self):
            self.is_open = False

    monkeypatch.setattr(pika, "BlockingConnection", FakeBlockingConnection)
    connection = PikaConnection(
        "amqp://guest:guest@localhost/%2F",
        heartbeat_seconds=30,
        blocked_connection_timeout_seconds=30,
        socket_timeout_seconds=10,
    )

    connection.connect()

    parameters = captured["parameters"]
    assert parameters.heartbeat == 30
    assert parameters.blocked_connection_timeout == 30
    assert parameters.socket_timeout == 10
    assert captured["confirm_delivery"] is True


def test_rpc_publisher_confirm_nack_is_transport_failure():
    class FakeChannel:
        def __init__(self):
            self.cancelled = []

        def basic_consume(self, **_kwargs):
            return "consumer-1"

        def basic_publish(self, **_kwargs):
            raise NackError([])

        def basic_cancel(self, tag):
            self.cancelled.append(tag)

    channel = FakeChannel()
    connection = PikaConnection("amqp://unused")
    connection._connection = SimpleNamespace(is_open=True)
    connection._channel = channel

    with pytest.raises(TransportUnavailableError, match="publish/reply transport"):
        connection.rpc_call("trex.slots.req.default", b"{}", "corr-1", 1.0)

    assert channel.cancelled == ["consumer-1"]


def test_wait_with_heartbeats_preserves_requested_wait_when_event_pump_returns_early(monkeypatch):
    connection = PikaConnection("amqp://unused")
    monotonic_values = iter([0.0, 0.0, 0.5, 1.1])
    pumped = []
    monkeypatch.setattr(
        "trex_tvr.transport.connection.time.monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(connection, "process_events", lambda time_limit: pumped.append(time_limit))

    connection.wait_with_heartbeats(1.0)

    assert pumped == [1.0, 0.5]


def test_close_clears_stale_connection_even_when_graceful_close_fails():
    class StaleConnection:
        is_open = True

        def close(self):
            raise OSError("stream already reset")

    connection = PikaConnection("amqp://unused")
    connection._connection = StaleConnection()  # type: ignore[assignment]
    connection._channel = object()  # type: ignore[assignment]

    connection.close()

    assert connection._connection is None
    assert connection._channel is None
