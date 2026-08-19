"""Shared pika connection for the transport clients.

Holds one BlockingConnection + channel. Constructing the object does NOT connect;
call `connect()` at daemon startup (fail-closed). Using a client before `connect()`
raises `TransportUnavailableError` (no local buffering).
"""
from __future__ import annotations

import time

from trex_tvr.transport.base import RpcTimeoutError, TransportUnavailableError

_DIRECT_REPLY_TO = "amq.rabbitmq.reply-to"


class PikaConnection:
    """Thin wrapper over a pika BlockingConnection (lazy connect)."""

    def __init__(
        self,
        rabbitmq_url: str,
        *,
        heartbeat_seconds: int = 30,
        blocked_connection_timeout_seconds: int = 30,
        socket_timeout_seconds: int = 10,
    ) -> None:
        self.rabbitmq_url = rabbitmq_url
        self.heartbeat_seconds = heartbeat_seconds
        self.blocked_connection_timeout_seconds = blocked_connection_timeout_seconds
        self.socket_timeout_seconds = socket_timeout_seconds
        self._connection = None
        self._channel = None

    def connect(self) -> None:
        import pika  # imported here so the module loads without a live broker.

        try:
            parameters = pika.URLParameters(self.rabbitmq_url)
            parameters.heartbeat = self.heartbeat_seconds
            parameters.blocked_connection_timeout = self.blocked_connection_timeout_seconds
            parameters.socket_timeout = self.socket_timeout_seconds
            self._connection = pika.BlockingConnection(parameters)
            self._channel = self._connection.channel()
            self._channel.confirm_delivery()
        except pika.exceptions.AMQPError as exc:
            self.close()
            raise TransportUnavailableError(f"RabbitMQ connection/setup error: {exc}") from exc

    @property
    def connected(self) -> bool:
        return self._channel is not None and getattr(self._connection, "is_open", False)

    @property
    def channel(self):
        if self._channel is None:
            raise TransportUnavailableError("pika channel not connected; call connect() first")
        return self._channel

    @property
    def connection(self):
        if self._connection is None:
            raise TransportUnavailableError("pika connection not established; call connect() first")
        return self._connection

    def process_events(self, time_limit: float) -> None:
        import pika

        try:
            self.connection.process_data_events(time_limit=time_limit)
        except pika.exceptions.AMQPError as exc:
            raise TransportUnavailableError(f"RabbitMQ idle heartbeat error: {exc}") from exc

    def wait_with_heartbeats(self, duration: float) -> None:
        """Wait for the cadence remainder while continuously servicing AMQP I/O."""
        deadline = time.monotonic() + duration
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            # process_data_events may return early after any event/timer. Looping
            # preserves the cadence floor while servicing heartbeats promptly.
            self.process_events(time_limit=min(1.0, remaining))

    def rpc_call(
        self,
        routing_key: str,
        body: bytes,
        correlation_id: str,
        timeout: float,
        delivery_mode: int = 1,
    ) -> bytes:
        """RPC over RabbitMQ direct reply-to. Raises TransportUnavailableError if not
        connected and RpcTimeoutError if no matching reply arrives within `timeout`.
        """
        import pika

        channel = self.channel  # raises TransportUnavailableError if not connected
        holder: dict[str, bytes] = {}

        def _on_reply(_ch, _method, props, payload: bytes) -> None:
            if props.correlation_id == correlation_id:
                holder["body"] = payload

        consumer_tag = None
        try:
            consumer_tag = channel.basic_consume(
                queue=_DIRECT_REPLY_TO, on_message_callback=_on_reply, auto_ack=True
            )
            confirmed = channel.basic_publish(
                exchange="",
                routing_key=routing_key,
                properties=pika.BasicProperties(
                    reply_to=_DIRECT_REPLY_TO,
                    correlation_id=correlation_id,
                    delivery_mode=delivery_mode,
                ),
                body=body,
            )
            if confirmed is False:
                raise TransportUnavailableError(
                    "RabbitMQ publisher confirm negative acknowledgement"
                )
            deadline = time.monotonic() + timeout
            while "body" not in holder:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RpcTimeoutError(f"no reply for correlation_id={correlation_id!r}")
                self.connection.process_data_events(time_limit=min(0.5, remaining))
        except pika.exceptions.AMQPError as exc:
            raise TransportUnavailableError(f"RabbitMQ publish/reply transport error: {exc}") from exc
        finally:
            if consumer_tag is not None:
                try:
                    channel.basic_cancel(consumer_tag)
                except Exception:  # noqa: BLE001 — best-effort cleanup.
                    pass
        return holder["body"]

    def reconnect(self) -> None:
        """Close existing connection and re-establish (used by daemon backoff recovery)."""
        self.close()
        self.connect()

    def close(self) -> None:
        try:
            if self._connection is not None and getattr(self._connection, "is_open", False):
                try:
                    self._connection.close()
                except Exception:  # noqa: BLE001 — stale/reset streams cannot close cleanly.
                    pass
        finally:
            self._connection = None
            self._channel = None
