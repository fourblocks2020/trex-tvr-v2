# trex-tvr-v1

**Target-Value Rebalancer (TVR) v1** — constant-mix 목표비율 리밸런싱 장기전략 데몬.

보유 포트폴리오의 종목별 비율이 목표(target weight)에서 임계치 이상 벗어날 때
**매수 또는 부분매도 인텐트**를 발행해 비율을 복원하는 장기(중장기) 전략 데몬이다.

**BAT-001 구현 완료** (2026-07-04):
- `TvrStrategy.evaluate()`: 실제 constant-mix 리밸런싱 로직 (비중·밴드·dust·whole-share)
- sell-intent 경로 신설: `IntentPort.submit_sell` + `build_sell_intent` + 멱등 intent_id(`sell:...`)
- decision-cycle 리밸런싱형: inactive+holding 평가, buy/sell 분기, strategy_params 폴백
- status/health 스냅샷 (REQ-004 day-1 관측성): `tvr status` CLI + `/tmp/trex_tvr_status.json`

## Architecture

```
                ┌─────────────────────────────────────────────────────┐
                │  per-slot strategy_params (portfolio-hub → trex-core → TVR)  │
                │  target_weight, rebalance_band_pct, min_trade_krw             │
                └───────────────────────────────┬─────────────────────┘
                                                │
 ┌─────────────────┐    slots.req              ▼
 │   trex-core     │ ◄──── strategy:"tvr_v1" ──── TvrStrategy.evaluate()
 │ (HOLDING slots) │                              pure deterministic function
 │ (INACTIVE slots)│ ────► SellSignal / BuySignal / HoldDecision
 └─────────────────┘               │
                                   └── transport: submit_buy / submit_sell
```

## Seam rules (enforced by `tests/test_import_sweep.py`)

- `strategies/` must NOT import `transport`, `market_data`, `redis`, `pika`, `httpx`
- `trex_tvr/` must NOT import `trex_core` (pure AMQP wire contract only)
- Strategy `evaluate()` must be deterministic (no IO, no clock, no random)

## Quickstart

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q   # import sweep + tvr strategy + engine + observability tests
```

### Run
```bash
cp config/.env.example config/.env   # fill in TREX_RABBITMQ_URL / KIS / REDIS
trex-tvr run --config instance.example --verbose
```

### Check status
```bash
trex-tvr status                  # reads /tmp/trex_tvr_status.json
trex-tvr status --path /var/run/tvr_status.json
```

### Backtest (strategy-agnostic)
```bash
python scripts/run_dump.py --from 20260501 --to 20260612
trex-tvr-backtest --config instance.example --date-range 20260501:20260612 \
  --dump-dir data/dump --capacity 20
```

## Contract notes

- **slot_id is the canonical key** (buy and sell intents).
- **TVR exit**: TVR is a pure rebalancing strategy — it does NOT declare stop_loss/take_profit
  on buy intents. The `[exit]` config section is optional and omitted for TVR.
- **Idempotency**: buy intent는
  `buy:{strategy_id}:{instance}:{trading_date}:{slot_id}:{decision_at}`, sell intent는
  `sell:{strategy_id}:{instance}:{trading_date}:{slot_id}:{decision_at}`을 사용한다.
  같은 authoritative snapshot decision의 transport duplicate는 같은 키를 유지하고,
  이후 authoritative snapshot의 독립 decision은 새 키를 사용한다.
- **Snapshot-only execution boundary**: TVR은 core slot snapshot만 전략 판단 입력으로
  사용한다. broker/fill event, retryability 또는 attempt identity를 구독하지 않는다.
- **RabbitMQ resilience**: TVR typed config가 heartbeat 30초, blocked connection timeout
  30초, socket timeout 10초의 기본 profile을 소유한다. 같은 tuning 값을
  `TREX_RABBITMQ_URL` query에 중복 지정하면 credential을 출력하지 않고 startup을
  거부한다. Slot/intent RPC request는 publisher confirm을 사용하지만 confirm은
  RabbitMQ broker acceptance일 뿐 core admission 또는 주문 실행 완료가 아니다.
- **Ambiguous intent outcome**: intent RPC 중 reset/timeout이 발생하면 TVR은 broker
  결과를 추론하거나 local retry/fresh intent를 만들지 않는다. 기존 cycle을 중단하고
  reconnect한 뒤 다음 cycle의 authoritative core slot snapshot으로 재수렴한다.
- **Venue**: TVR buy/sell intent는 `venue`를 항상 포함한다. NXT-only 구간은 `NXT`, 그 외 tradeable session은 `KRX`로 라우팅한다.
- **strategy_params** (target_weight etc.) come from portfolio-hub `strategy_sets` → trex-core
  slot row → daemon eval_asset. TVR config TOML carries runtime settings only.
- **trex-core wire dependencies** (strategy filter + row strategy_params) are additive changes
  not yet deployed; TVR handles missing_strategy_params via fallback Diagnostic (slots skipped).
- Requires Redis URL (holiday cache).

## Layout
```
trex_tvr/
  strategies/   base.py (BuySignal/SellSignal/HoldDecision/Diagnostic contract)
                registry.py · tvr_strategy.py (TvrStrategy — rebalancing logic)
  market_data/  kis_data_hub_client.py · holiday_cache.py
  transport/    base.py · connection.py · slot_client.py
                intent_client.py (submit_buy + submit_sell)
  runtime/      daemon.py (rebalancing engine) · config.py · intent_builder.py
                status.py (StatusSnapshot · write_snapshot · read_snapshot)
                cli.py (run | status subcommands)
  backtest/     replay_engine.py · exit_simulator.py · metrics.py · dump_loader.py
                dump_job.py · universe.py · bt_config.py · cli.py
scripts/        run_dump.py
tests/          test_import_sweep.py (seam) · strategies/ · runtime/ · backtest/
config/         instance.example.toml · .env.example · backtest_universe.csv
docs/           SDLC baseline + batches/bat-001_20260704_tvr-rebalancing-v1/
```
