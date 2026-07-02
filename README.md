# trex-tvr-v1

**Target-Value Rebalancer (TVR) v1** — constant-mix 목표비율 리밸런싱 장기전략 데몬.

보유 포트폴리오의 종목별 비율이 목표(target weight)에서 임계치 이상 벗어날 때
매수 인텐트를 발행해 비율을 복원하는 장기(중장기) 전략 데몬이다.

> **Scaffolded from `trex-strategy-template`.**
> TVR 전략 로직(목표비율 편차 평가)은 Discovery/REQ 이후 구현 예정.
> 현재는 스캐폴드 + baseline 상태.

## TODO(설계): 리밸런싱 sell-intent 경로

템플릿 프레임은 **매수 전용** (transport가 sell intent 미발신, 청산=core 위임)이다.
TVR은 **자체 부분매도(SellIntent.qty)** 로 리밸런싱해야 하므로, 이후 구현에서
**transport/intent_builder에 sell-intent(부분매도) 경로 추가 + decision-cycle을
리밸런싱형으로 확장**이 필요하다. 이는 Discovery/설계 항목이다.

## What you implement vs. what you reuse

| Reuse as-is (the frame) | Replace (your strategy) |
|---|---|
| `transport/` — slots/intent/events/connection (pure AMQP) | `strategies/example_strategy.py` → `TvrStrategy` |
| `market_data/kis_data_hub_client.py`, `holiday_cache.py` | `[strategy.tvr_v1]` params in the instance TOML |
| `runtime/daemon.py` — the decision-cycle engine | `tests/strategies/test_example_strategy.py` |
| `runtime/config.py`, `cli.py`, `intent_builder.py` | the `register(...)` call in `strategies/registry.py` |
| `strategies/base.py` — the `BuyStrategy` contract | TVR 파라미터(target_weight, rebalance_threshold 등) |

The **seam** (`strategies/` imports no infra; nothing imports `trex_core`) is enforced
by `tests/test_import_sweep.py` — keep your strategy a pure, deterministic function.

## Quickstart

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q          # import sweep + tvr scaffold strategy + engine smoke
```

### Run
```bash
cp config/.env.example config/.env   # fill in broker / kis-data-hub / redis
trex-tvr --config instance.example --verbose
```

### Backtest (strategy-agnostic)
```bash
# 1) Download a candle dump
python scripts/run_dump.py --from 20260501 --to 20260612

# 2) Backtest
trex-tvr-backtest --config instance.example --date-range 20260501:20260612 \
  --dump-dir data/dump --capacity 20
```

## Contract notes (inherited from trex-strategy-template / trex-core)
- **slot_id is the canonical buy key** (round-tripped from `trex.slots.req`).
- **Exit is delegated to trex-core** via `stop_loss_pct`/`take_profit_pct` on the buy
  intent (`[exit]` config). The daemon does not send sell intents in the current scaffold.
  *(TVR 리밸런싱 매도는 sell-intent 경로 설계 후 추가 예정.)*
- **Idempotency**: `intent_id = {strategy_id}:{trading_date}:{slot_id}`.
- Requires a `safety_sell_enabled` trex-core instance and a Redis URL (holiday cache).

## Layout
```
trex_tvr/
  strategies/   base.py (contract) · registry.py · example_strategy.py (TvrStrategy)
  market_data/  kis_data_hub_client.py · holiday_cache.py
  transport/    base.py · connection.py · slot_client.py · intent_client.py · events_consumer.py
  runtime/      daemon.py (engine) · config.py · intent_builder.py · cli.py
  backtest/     replay_engine.py · exit_simulator.py · metrics.py · dump_loader.py · dump_job.py · universe.py · bt_config.py · cli.py
scripts/        run_dump.py
tests/          test_import_sweep.py (seam) · strategies/ · runtime/ · backtest/
config/         instance.example.toml · .env.example · backtest_universe.csv
docs/           SDLC baseline 문서 + active index (.stage-pilot/bootstrap/baseline.yaml)
```
