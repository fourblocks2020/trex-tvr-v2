from __future__ import annotations

import os
import subprocess
import threading
import time

import pika
import pytest

from trex_tvr.transport.base import TransportUnavailableError
from trex_tvr.transport.connection import PikaConnection


_RABBITMQ_URL = os.environ.get("TVR_TEST_RABBITMQ_URL")
pytestmark = pytest.mark.skipif(
    not _RABBITMQ_URL,
    reason="set TVR_TEST_RABBITMQ_URL for disposable RabbitMQ integration",
)


def test_real_rabbitmq_confirm_rpc_and_reconnect_round_trip():
    queue_name = "trex.test.issue6.rpc"
    ready = threading.Event()
    stop = threading.Event()
    errors: list[BaseException] = []

    def serve() -> None:
        connection = None
        try:
            connection = pika.BlockingConnection(pika.URLParameters(_RABBITMQ_URL))
            channel = connection.channel()
            channel.queue_declare(queue=queue_name, durable=False, auto_delete=True)

            def reply(ch, method, properties, body):
                ch.basic_publish(
                    exchange="",
                    routing_key=properties.reply_to,
                    properties=pika.BasicProperties(correlation_id=properties.correlation_id),
                    body=b'{"ok":true}',
                )
                ch.basic_ack(method.delivery_tag)

            channel.basic_consume(queue=queue_name, on_message_callback=reply)
            ready.set()
            while not stop.is_set():
                connection.process_data_events(time_limit=0.1)
        except BaseException as exc:  # surfaced in the test thread
            errors.append(exc)
            ready.set()
        finally:
            if connection is not None and connection.is_open:
                connection.close()

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    assert ready.wait(10), "RabbitMQ responder did not become ready"
    assert not errors

    client = PikaConnection(
        _RABBITMQ_URL,
        heartbeat_seconds=30,
        blocked_connection_timeout_seconds=30,
        socket_timeout_seconds=10,
    )
    try:
        client.connect()
        assert client.rpc_call(queue_name, b"{}", "first", 5.0) == b'{"ok":true}'
        # BlockingConnection only services heartbeats while its I/O loop runs.
        # Keep the connection idle longer than one configured heartbeat interval.
        client.wait_with_heartbeats(35)
        assert client.connected
        assert client.rpc_call(queue_name, b"{}", "after-idle", 5.0) == b'{"ok":true}'
        client.reconnect()
        assert client.rpc_call(queue_name, b"{}", "second", 5.0) == b'{"ok":true}'
    finally:
        client.close()
        stop.set()
        server.join(timeout=10)

    assert not server.is_alive()
    assert not errors


@pytest.mark.skipif(
    not os.environ.get("TVR_TEST_RABBITMQ_CONTAINER"),
    reason="set TVR_TEST_RABBITMQ_CONTAINER for broker restart fault injection",
)
def test_real_rabbitmq_forced_broker_restart_is_detected_and_reconnects():
    container = os.environ["TVR_TEST_RABBITMQ_CONTAINER"]
    client = PikaConnection(
        _RABBITMQ_URL,
        heartbeat_seconds=30,
        blocked_connection_timeout_seconds=30,
        socket_timeout_seconds=10,
    )
    try:
        client.connect()
        subprocess.run(
            ["docker", "restart", container],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        with pytest.raises(TransportUnavailableError):
            client.wait_with_heartbeats(5)

        for _ in range(60):
            try:
                probe = pika.BlockingConnection(pika.URLParameters(_RABBITMQ_URL))
                probe.close()
                break
            except pika.exceptions.AMQPError:
                time.sleep(0.25)
        else:
            pytest.fail("RabbitMQ did not become ready after restart")

        client.reconnect()
        assert client.connected
    finally:
        client.close()
