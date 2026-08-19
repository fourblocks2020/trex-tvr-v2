# GitHub 구현 완료 comment snapshot

- Source: https://github.com/fourblocks2020/trex-tvr-v1/issues/6#issuecomment-5341044595
- Repository: `fourblocks2020/trex-tvr-v1`
- Issue: `#6`
- Captured from live GitHub comment after read-back verification on 2026-08-19.
- Classification: repository-local evidence snapshot; not a canonical ProofLine lifecycle artifact.

---

## 구현 완료

- **대상:** RabbitMQ Transport Reliability / Resilience
- **브랜치:** `main`
- **기준:** `origin/main` (`1cb4584cbc3d8695e61fff776690fd3c9cf9b3b7`)
- **로컬 구현 커밋:** `7d28d46dcf986ce5ce64997a5e9a2870e24048c9`
- **완료 시각:** 2026-08-19 19:46:45 KST

### 구현 내용

- TVR typed RabbitMQ 설정에 heartbeat 30초, blocked connection timeout 30초, socket timeout 10초를 반영했다.
- RabbitMQ URL에 동일 tuning parameter가 중복되면 credential을 출력하지 않고 startup을 실패시키도록 했다.
- Slot RPC와 intent admission RPC의 공용 request 경로에 publisher confirm을 적용했다.
- Publisher confirm을 RabbitMQ broker acceptance로만 취급하고 core admission·주문 생성·체결 완료와 구분했다.
- Pika `BlockingConnection`이 daemon cadence 대기 중에도 heartbeat와 AMQP I/O를 처리하도록 idle wait를 연결했다.
- Idle/reset/publish-reply transport failure를 단계별 `TransportUnavailableError`로 구분하고 stale connection close 실패 후에도 reconnect할 수 있도록 했다.
- Intent reply 유실의 ambiguous outcome에서는 local retry나 새 intent를 만들지 않고, reconnect 후 다음 authoritative core slot snapshot으로 수렴하는 경계를 유지·검증했다.
- 운영 계약과 예제 설정을 README 및 `config/instance.example.toml`에 기록했다.

### 실제 검증 결과

- **전체 회귀:** `218 passed, 2 skipped`
- **Disposable RabbitMQ 4.1 integration:** `2 passed`
  - Publisher-confirm RPC 및 direct reply-to round-trip
  - 35초 idle heartbeat 처리
  - Reconnect round-trip
  - 실제 RabbitMQ container restart에 의한 강제 단절 감지 및 reconnect
- **Ambiguous intent recovery:** buy/sell 각각 다음 순서를 실제 `Daemon.run_forever()` 경로로 검증
  - 최초 slot snapshot → intent 1회 제출 → reply 유실 → reconnect → `pending_buy`/`pending_sell` snapshot 선조회 → 신규 intent 미발행
- **변경 범위 Ruff:** PASS
- **`git diff --check`:** PASS
- **`uv build`:** sdist/wheel 생성 PASS
- **독립 재-QC:** Blocker 0 / Major 0 / Minor 0, **PASS**

### 비차단 후속 권고

- 전체-tree Ruff에는 이번 변경과 무관한 기존 테스트 파일의 baseline 오류 5건이 남아 있다. 이번 변경 범위 Ruff는 통과했으며 별도 정리 대상으로 권고한다.
- Production RabbitMQ 설정 변경 전 실제 connection URL에 application-owned tuning query가 없는지 precheck할 것을 권고한다.

### 완료 경계

- 완료된 범위는 **로컬 `main`의 구현·검증·커밋**이다.
- **push, PR 생성, 원격 `main` 반영, artifact/image 게시, production RabbitMQ 설정 변경, deployment/restart, 실제 주문 실행, GitHub Release 발행은 수행하지 않았다.**
- 이 댓글은 제품 코드의 로컬 구현 완료를 의미하며 배포·릴리스 또는 운영 lifecycle 완료를 의미하지 않는다.
