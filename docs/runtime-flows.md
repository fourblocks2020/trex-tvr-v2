# Trex TVR v1 Runtime Flows

- Baseline Mode: observed
- Baseline Seed: .stage-pilot/bootstrap/baseline.yaml
- Status: draft
- Owner: service-dev (범용 에이전트)
- Last Updated (KST): 2026-07-02
- Source Discovery / Batch: bootstrap-baseline (scaffold)
- Primary Runtime: worker

## Project Summary
- constant-mix 목표비율 리밸런싱 장기전략 데몬. trex-core 슬롯 코어와 순수 AMQP wire 계약으로 연동.

## Purpose
- This document is the current approved execution-flow baseline for the project.
- It is a cross-cutting reference document for Discovery, REQ, Batch Design, Batch Verification, and implementation updates.

## Scope
- Covers representative command and runtime flows.
- Does not duplicate full acceptance criteria or low-level implementation details.

## Covered Entry Points

- trex-tvr :: TVR 리밸런싱 전략 데몬 (polling loop)
- trex-tvr-backtest :: 전략-무관 백테스트 (로컬 덤프 시간 리플레이)

## Shared Components

- `trex_tvr.transport.connection` — AMQP 연결 관리 (backoff reconnect)
- `trex_tvr.market_data.kis_data_hub_client` — 현재가/분봉 조달
- `trex_tvr.market_data.holiday_cache` — Redis holiday 캐시
- `trex_tvr.strategies.registry` — 전략 instantiate / schedule 해석

## Flow: `daemon startup`

1. `trex-tvr --config instance.example` 실행
2. config 로드 + 검증 (fail-closed: 실패 시 기동 거부)
3. `ensure_default_strategies_loaded()` — TvrStrategy(tvr_v1) 레지스트리 등록
4. `precheck_instantiate_all()` — 전략 instantiate 사전 검사
5. AMQP 연결 수립 (backoff reconnect)
6. 폴링 루프 진입

## Flow: `decision cycle (polling loop)`

1. KST 시각 체크 — schedule `[start, end)` 밖이면 sleep(poll_interval)
2. holiday 캐시 확인 — 공휴일이면 skip
3. snapshot RPC (`trex.snapshot.req`) — universe + available_slots 조회
4. available_slots == 0 이면 capacity pre-flight 실패 → skip
5. 각 asset에 대해:
   a. 현재가 fetch (`kis_data_hub_client.get_quote`)
   b. `TvrStrategy.evaluate(asset, snapshot, market_data)` 호출
   c. BuySignal → intent_builder → buy intent RPC (`trex.intent.req`)
   d. HoldDecision / Diagnostic → 로그 후 다음 asset
6. sleep(poll_interval) → 루프 반복

## Flow: `backtest`

1. `trex-tvr-backtest --config instance.example --date-range ... --dump-dir ...`
2. 로컬 분봉 덤프 로드 (`dump_loader`)
3. 날짜 범위 내 분봉을 시간순 리플레이
4. 각 bar에서 전략 evaluate → BuySignal 시 포지션 진입
5. exit_simulator로 SL/TP/forced-close 시뮬레이션 (look-ahead = 0)
6. metrics 집계(P&L, win rate, MDD 등) + 리포트 출력

## Flow Constraints

- `strategies.evaluate()` 는 순수 결정론적 함수 (IO/clock/random 없음).
- transport는 연결 상태에서만 동작 (TransportUnavailableError = 즉시 실패, 로컬 버퍼 없음).
- intent 멱등키 = `{strategy_id}:{trading_date}:{slot_id}` — trex-core가 dedup.
- 슬롯 상태 단일 writer = trex-core. 데몬은 buy intent 제출만 함.

## Current Gaps / Planned Changes

- 리밸런싱 매도 플로 미구현 — 현재 플로는 매수만. 매도 경로는 별도 설계 필요.
- TvrStrategy.evaluate() 실제 리밸런싱 로직 미구현 — 현재 scaffold placeholder.

## Update Triggers

- Update this document when a command entry point changes.
- Update this document when a runtime orchestration path changes.
- Update this document when a batch changes the approved flow baseline.

## Change Log

- 2026-07-02: bootstrap-baseline scaffold — initial draft.
