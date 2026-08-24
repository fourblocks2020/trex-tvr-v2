# Trex TVR v1 Interface Contract

- Baseline Mode: observed
- Baseline Seed: .stage-pilot/bootstrap/baseline.yaml
- Status: draft
- Owner: service-dev (범용 에이전트)
- Last Updated (KST): 2026-07-02
- Source Discovery / Batch: bootstrap-baseline (scaffold)
- Primary Runtime: worker

## Project Summary
- constant-mix 목표비율 리밸런싱 장기전략 데몬. trex-core 슬롯 코어와 순수 AMQP wire 계약으로 연동.

## Primary Domain
- 자동매매 장기전략 — 목표비율(constant-mix) 리밸런싱 매수

## Purpose
- This document captures the approved external and cross-boundary interface contracts for the project.
- It is a cross-cutting reference for REQ drafting, batch design, implementation, verification, and handoff.

## Scope
- Covers externally visible APIs, CLI surfaces, events, files, or batch I/O contracts.
- Does not duplicate full business requirements already owned by REQ documents.

## Interface Summary

- trex-core RabbitMQ wire 계약 :: slot snapshot/intent RPC transport (consumed)
- kis-data-hub HTTP client :: 현재가/분봉 조달 (consumed)
- trex-tvr-v1 CLI/config :: 운영자 기동 인터페이스 (own)

## Interface: `trex-core RabbitMQ wire 계약`

- Type: event
- Consumers / Producers: trex-tvr-v1 데몬(클라이언트) ↔ trex-core 데몬(서버), RabbitMQ broker가 transport
- Purpose: slot snapshot RPC로 현재 core 상태를 읽고 buy/sell intent RPC를 제출한다. broker/fill event는 전략 입력으로 구독하지 않는다.
- Stability: experimental

### Inputs

- `trex.slots.req` :: `{filter: inactive,holding, strategy: tvr_v1}` — 현재 slot snapshot 조회
- `trex.intent.req` buy/sell :: `{intent_type, intent_id, slot_id, qty, venue, ...}` — admission 제출. `venue`는 필수이며 `"NXT"` 또는 `"KRX"` 중 하나다.

### Outputs

- slot snapshot 응답 :: `{slots[], available_slots, degraded, reason, message}` (item에 price 없음)
- intent 응답 :: `{intent_id, status: admitted|rejected, slot_id?, reason?}`

### Error Contract

- `TransportUnavailableError` :: connected 아닐 때 즉시 실패 (로컬 버퍼링 없음)
- `RpcTimeoutError` :: timeout 안에 reply 없음
- degraded envelope :: `reason: portfolio_hub_unavailable|snapshot_fetch_failed|cash_position_stale`

### Compatibility Rules

- wire 계약 소유자는 trex-core. trex-tvr-v1은 JSON payload 호환만 공유, `trex_core` 코드 무import.
- intent `correlation_id`(=`intent_id`)가 멱등키 — core `intent_log`가 dedup.
- TVR venue routing: NXT-only (`08:00:00 <= t < 08:50:00`, `15:30:00 <= t < 20:00:00`) + slot `markets`에 `NXT` 포함 → `venue="NXT"`. 그 외 tradeable session의 eligible slot → `venue="KRX"`.
- 기존 core #61 omitted venue→SOR contract는 TVR 기준 superseded다. TVR은 더 이상 intent에서 `venue`를 생략하지 않는다.
- TVR은 KIS failure, broker rejection detail, attempt identity, retryability, timeout 또는 fill/cancel raw event를 소비·저장하지 않는다. 다음 slot snapshot만 후속 판단에 사용한다.

## Interface: `kis-data-hub HTTP client`

- Type: other
- Consumers / Producers: trex-tvr-v1 시세 어댑터(소비) → kis-data-hub
- Purpose: 현재가 및 분봉 조달 (core snapshot에 price 없음).
- Stability: experimental

### Inputs

- `get_quote(code, allow_stale)` :: POST /v1/quotes/latest
- `get_candles(code, count)` :: POST /v1/candles/minute
- `check_health()` :: GET /health
- `check_holiday(date)` :: GET /v1/market/holiday-check (선택)

### Outputs

- quote :: `{... price ...}`
- candles :: `list[{datetime, open, high, low, close, volume}]`

### Error Contract

- `KisDataHubClientError(error_class ∈ {auth, network, http, schema_drift, other})`

### Compatibility Rules

- Bearer 인증.

## Interface: `trex-tvr-v1 CLI / config`

- Type: cli
- Consumers / Producers: 운영자
- Purpose: per-instance TOML(--config) + env로 데몬 기동/튜닝.
- Stability: experimental

### Inputs

- `trex-tvr --config <path>` :: `instance_key, rabbitmq_url, kis_data_hub_base_url/api_key, schedule, strategy.tvr_v1 params`
- `trex-tvr-backtest --config <path> --date-range ... --dump-dir ...`
- env: 시크릿(api key) 등

### Outputs

- 장기 실행 데몬 프로세스 + 구조적 로그/감사
- 백테스트 P&L 리포트 (stdout)

### Error Contract

- config 검증 실패 시 fail-closed 기동 거부

### Compatibility Rules

- trex-core의 --config per-instance 컨벤션과 정합 (instance_key 분리).

## Shared Constraints

- 슬롯 상태의 단일 writer는 trex-core. trex-tvr-v1은 슬롯/원장을 직접 쓰지 않고 읽기 + buy intent 제출만 한다.
- exit(청산)는 매수 intent의 `stop_loss_price`/`take_profit_price`로 선언 → trex-core SafetySellService 자율 수행.

## Current Gaps / Planned Changes

- 리밸런싱 sell-intent 인터페이스 미정의 — TVR 매도 경로 설계(Discovery) 이후 이 문서에 추가 필요.
- TVR 파라미터(target_weight 등) TOML 스키마 미확정.

## Update Triggers

- Update this document when an externally visible API, event, CLI, or file contract changes.
- Update this document when a batch changes compatibility, validation, or error behavior.
- Update this document when a new external consumer or producer is introduced.

## Change Log

- 2026-07-02: bootstrap-baseline scaffold — initial draft.
