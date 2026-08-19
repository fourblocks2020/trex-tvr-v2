# Trex TVR v1 Project Structure

- Baseline Mode: observed
- Baseline Seed: .stage-pilot/bootstrap/baseline.yaml
- Status: draft
- Owner: service-dev (범용 에이전트)
- Last Updated (KST): 2026-07-02
- Source Discovery / Batch: bootstrap-baseline (scaffold)

## Project Summary
- constant-mix 목표비율 리밸런싱 장기전략 데몬. 포트폴리오 종목별 비율이 목표에서 벗어날 때 매수 인텐트를 발행해 비율을 복원한다.

## Primary Domain
- 자동매매 장기전략 — 목표비율(constant-mix) 리밸런싱 매수

## Tech Stack
- Python >=3.12
- pyproject.toml 기반 패키지(setuptools), 배포명 trex-tvr-v1 / 패키지 trex_tvr
- pytest(단위 테스트), ruff(린트)
- pika — RabbitMQ AMQP 클라이언트(trex-core wire 계약, 코드 무의존)
- httpx — kis-data-hub 시세 클라이언트
- redis(선택) — holiday 캐시
- per-instance TOML(--config) + .env 설정

## Primary Runtime
- Type: worker
- Planned Entry Points:
  - trex-tvr :: TVR 리밸런싱 전략 데몬 (--config 로 기동)
  - trex-tvr-backtest :: 전략-무관 백테스트 (로컬 덤프 시간 리플레이)

## Purpose
- This document is the current approved repository and package structure baseline.
- It is a cross-cutting reference document for Discovery, REQ, Batch Design, Batch Verification, and implementation updates.

## Scope
- Covers the top-level repository layout and package/module boundaries.
- Does not duplicate detailed business rules, acceptance criteria, or implementation-only function signatures.

## Top-Level Structure

```text
trex-tvr-v1/
├── trex_tvr/                  # 메인 패키지
│   ├── strategies/            # TvrStrategy + base + registry (시임: 인프라 무import)
│   ├── market_data/           # kis-data-hub 클라이언트 + holiday 캐시
│   ├── transport/             # RabbitMQ wire 클라이언트 (pika, trex_core 무import)
│   ├── runtime/               # daemon 엔진 + config + CLI
│   └── backtest/              # 전략-무관 백테스트 엔진
├── tests/
│   ├── test_import_sweep.py   # 시임 규칙 검사 (strategies/ 인프라 무import, trex_core 무import)
│   ├── strategies/            # TvrStrategy 단위 테스트
│   ├── runtime/               # daemon cycle 스모크 테스트
│   └── backtest/              # 백테스트 컴포넌트 테스트
├── config/
│   ├── instance.example.toml  # 인스턴스 설정 예시 (strategy.tvr_v1)
│   ├── .env.example           # 시크릿/인프라 URL 예시
│   └── backtest_universe.csv  # 백테스트 유니버스 예시
├── scripts/
│   └── run_dump.py            # 분봉 덤프 다운로더
├── docs/                      # SDLC baseline 문서 + active index
├── .stage-pilot/              # StagePilot SDLC 도구 (skills/templates/bootstrap)
├── CLAUDE.md                  # repo 고유 아키텍처 규칙 + SDLC governance
├── pyproject.toml             # 패키지 설정 (trex-tvr / trex-tvr-backtest 커맨드)
└── README.md
```

## Package / Module Responsibilities

- `trex_tvr/strategies/`
  - `base.py`: Strategy ABC + BuySignal/SellSignal/HoldDecision/Diagnostic dataclass (계약)
  - `tvr_strategy.py`: TvrStrategy constant-mix 리밸런싱 로직
  - `registry.py`: 전략 등록/instantiate/schedule 해석. 인프라 무import.
- `trex_tvr/market_data/`
  - `kis_data_hub_client.py`: httpx 기반 현재가/분봉 조달
  - `holiday_cache.py`: Redis 기반 거래일/공휴일 캐시
- `trex_tvr/transport/`
  - `connection.py`: pika AMQP 연결 관리 (backoff reconnect)
  - `slot_client.py`: snapshot RPC (universe + 가용 슬롯 조회)
  - `intent_client.py`: buy/sell intent RPC 제출
  - `events_consumer.py`: legacy compatibility module; runtime decision cycle에는 연결하지 않음
  - `base.py`: transport port ABC
- `trex_tvr/runtime/`
  - `daemon.py`: 결정-사이클 엔진 (schedule gate → capacity pre-flight → fan-out eval → intent)
  - `config.py`: per-instance TOML 파서
  - `intent_builder.py`: BuySignal/SellSignal → venue 없는 wire DTO 변환
  - `cli.py`: `trex-tvr` 커맨드 진입점
- `trex_tvr/backtest/`
  - 전략-무관 백테스트 (로컬 분봉 덤프 시간 리플레이, exit 시뮬레이터 포함)

## Dependency Rules

- `strategies/` 는 `transport`, `market_data`, `redis`, `pika`, `httpx`를 임포트하지 않는다.
- `trex_tvr/` 전체는 `trex_core`를 임포트하지 않는다 (순수 AMQP wire 계약).
- `runtime/`, `transport/`, `market_data/` 는 `strategies/`를 임포트할 수 있다.
- 이 규칙은 `tests/test_import_sweep.py`가 자동 검사한다.

## Shared Boundaries

- trex-core와의 경계: RabbitMQ wire 계약(JSON payload)만 공유. 코드 의존 없음.
- kis-data-hub와의 경계: HTTP REST API (Bearer 인증).

## Current Gaps / Planned Changes

- TVR 파라미터(target_weight, rebalance_threshold) 미확정.

## Update Triggers

- Update this document when package boundaries change.
- Update this document when new top-level runtime or domain modules are added.
- Update this document when a batch changes the approved structure baseline.

## Change Log

- 2026-07-02: bootstrap-baseline scaffold — trex-strategy-template에서 trex-tvr-v1으로 스캐폴드, initial draft.
