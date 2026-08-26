# Trex TVR v1 Data Model

- Baseline Mode: observed
- Baseline Seed: .stage-pilot/bootstrap/baseline.yaml
- Status: draft
- Owner: service-dev (범용 에이전트)
- Last Updated (KST): 2026-07-02
- Source Discovery / Batch: bootstrap-baseline (scaffold)

## Project Summary
- constant-mix 목표비율 리밸런싱 장기전략 데몬. trex-core 슬롯 코어와 순수 AMQP wire 계약으로 연동.

## Primary Domain
- 자동매매 장기전략 — 목표비율(constant-mix) 리밸런싱 매수

## Primary Persistence Backend
- 없음(stateless 전략 데몬) — 슬롯/원장 영속화는 trex-core(PostgreSQL)가 소유. 선택적 Redis로 holiday 캐시만 둔다.

## Purpose
- This document captures the approved cross-cutting data model baseline for the project.
- It is a shared reference for REQ drafting, batch design, implementation, verification, migration planning, and handoff.

## Scope
- Covers core entities, relationships, states, persistence mappings, and shared consistency rules.
- Does not duplicate full functional acceptance criteria already owned by REQ documents.

## Model Summary

- BuyIntent/SellIntent(wire DTO) :: TVR 평가 결과를 trex-core로 보내는 admission 요청
- HolidayCache(Redis) :: 거래일/공휴일 판정 캐시

## Entity: `BuyIntent / SellIntent (wire DTO)`

- Purpose: TVR evaluate()의 BuySignal/SellSignal을 trex-core admission 요청으로 변환한다.
- Source of Truth: RabbitMQ 메시지 (전이 권위는 trex-core `daily_slot`/`intent_log`)
- Lifecycle: BuySignal 발생 -> intent 발행 -> core admitted|rejected -> 다음 slot snapshot에서 상태 수렴 관측

### Key Fields

- `intent_id` :: str :: buy는 `buy:{strategy}:{instance}:{date}:{slot}:{decision_at}`, sell은 `sell:{strategy}:{instance}:{date}:{slot}:{decision_at}` — correlation_id이자 decision-scoped 멱등키
- `symbol` :: str :: 대상 종목 코드
- `qty` :: int? :: 주문 수량 (allocated_capital 대신 지정 가능)
- `allocated_capital` :: int? :: 배정 자본 (KRW)
- `entry_price` :: int :: 결정 시점 현재가 (역산 제거용)
- `strategy_id` :: str :: `"tvr_v1"`
- `venue` :: str :: `"NXT"` | `"KRX"` — session 기반 명시 라우팅 값
- `stop_loss_price` :: int? :: core safety-sell SL 가격 트리거
- `take_profit_price` :: int? :: core safety-sell TP 가격 트리거
- `forced_close_at` :: ISO-8601? :: 강제 마감 시각 (없으면 core default)

### Relationships

- Slot (trex-core 소유) :: admitted 시 idle -> pending_buy 슬롯과 1:1 결합

### State Rules

- buy intent는 한 snapshot decision의 `decision_at`을 키에 포함한다. 동일 decision의 transport 중복은 dedup하고 이후 authoritative snapshot의 새 전략 판단은 독립 키를 사용한다.
- sell intent는 한 snapshot decision의 `decision_at`을 키에 포함한다. 동일 decision의 transport 중복은 dedup하고 이후 holding snapshot의 새 전략 판단은 독립 키를 사용한다.
- buy/sell intent는 `venue`를 항상 포함한다. NXT-only session의 eligible slot은 `NXT`, 그 외 tradeable eligible slot은 `KRX`를 사용한다.

## Entity: `HolidayCache (Redis, 선택)`

- Purpose: 거래일/공휴일 판정 캐시 — 매 cycle kis-data-hub API 호출 회피.
- Source of Truth: Redis(선택; 없으면 kis-data-hub로 매번 조회)
- Lifecycle: 조회 시 set(TTL 24h) -> 같은 거래일 hit -> 다음 거래일 새 key

### Key Fields

- `key` :: str :: 거래일 날짜 기반 네임스페이스 (예: `trex:holiday:{date}`)
- `is_holiday` :: bool :: 공휴일/비거래일 여부

### Relationships

- kis-data-hub `/v1/market/holiday-check` :: 캐시 미스 시 조회

### State Rules

- 비권위 파생 캐시 — 유실 시 재조회, 정합성 부담 없음.

## Shared Consistency Rules

- 슬롯 상태/원장 기록의 단일 source of truth는 trex-core. trex-tvr-v1은 slot snapshot만 전략 판단 입력으로 읽는다.
- TVR은 broker outcome, retryability, attempt identity 또는 fill/cancel raw event를 durable state로 저장하지 않는다.
- intent 멱등키(`intent_id`)로 중복 매수 admission을 방지 (권위 dedup은 core `intent_log`).

## Persistence / Integration Notes

- trex-tvr-v1은 자체 DB를 두지 않는다. 관측/로그 외 영속 상태는 trex-core와 (선택) Redis에 둔다.

## Current Gaps / Planned Changes

- TVR 파라미터(target_weight, rebalance_threshold) 엔티티/저장 방식 미확정 — 첫 Discovery에서 정의.

## Update Triggers

- Update this document when a core entity, key field, or relationship changes.
- Update this document when lifecycle or persistence behavior changes.
- Update this document when a batch introduces a migration, backfill, or breaking data assumption.

## Change Log

- 2026-07-02: bootstrap-baseline scaffold — initial draft.
