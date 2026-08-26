"""trex-tvr-v1 — Target-Value Rebalancer (TVR) v1 장기전략 데몬 (pure AMQP).

Constant-mix 목표비율 리밸런싱 전략 데몬. trex-strategy-template 기반.
Layers (dependency direction is one-way: runtime -> {strategies, market_data, transport}):
  - strategies/   : pure decision layer (no infra imports; seam) — TvrStrategy
  - market_data/  : kis-data-hub HTTP adapter (candles/quote/holiday)
  - transport/    : trex-core RabbitMQ wire client (pika, pure AMQP; no trex_core import)
  - runtime/      : daemon orchestration (fan-out, gate stack, reconnect), config, intent builder, CLI
"""

__version__ = "0.1.1"
