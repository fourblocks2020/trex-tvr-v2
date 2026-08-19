# GitHub 구현 진행 상황 comment snapshot

- Source: https://github.com/fourblocks2020/trex-tvr-v1/issues/8#issuecomment-5338627301
- Repository: `fourblocks2020/trex-tvr-v1`
- Issue: `#8`
- Captured from live GitHub comment after read-back verification on 2026-08-19.
- Classification: repository-local evidence snapshot; not a canonical ProofLine lifecycle artifact.

---

## 구현 진행 상황

- **대상:** [trex-tvr-v1] discovery: requester-versioned GHCR 이미지 게시 계약
- **브랜치:** `main`
- **기준:** `main` (`6fdc42ff077d11ac48c57c08b364c77211d98987`)
- **상태:** 로컬 main 구현·검증·커밋 및 독립 재QC PASS — 미푸시, 미게시, 미배포
- **최종 업데이트:** 2026-08-19 16:11:15 KST

### 구현 계획 및 작업 목록

- [x] Python 3.12와 고정된 dependency lock을 사용해 재현 가능한 실행 환경을 만든다.
  - 개발 검증과 실제 image runtime이 같은 `uv.lock` 해석을 사용하고, tag·package·runtime version 불일치를 게시 전에 차단한다.
- [x] 검증된 source를 한 번만 build하는 non-root·read-only container 계약을 구현한다.
  - 운영 config와 secret은 image에 넣지 않고 read-only 주입하며, status 경로만 별도 writable 영역으로 둔다.
- [x] 일반 branch 검증과 strict stable version-tag GHCR 게시 권한을 분리한다.
  - branch/main은 registry에 쓰지 않고 검증만 수행하며, tag workflow는 exact source image를 재build하지 않고 게시한다.
- [x] image identity와 secret 비포함을 fail-closed로 검증한다.
  - source commit, OCI revision/version, image ID, archive SHA-256, SHA/version selector의 동일 digest와 moving `latest` 부재를 확인한다.
- [x] 설치된 image와 TVR dependency startup 경계를 실제 container로 검증한다.
  - CLI/import, isolated RabbitMQ·Redis·fake kis-data-hub startup gate와 initial status까지만 확인하고 strategy decision·core RPC·실제 주문은 주장하지 않는다.
- [x] bounded stop, config/secret/status mount와 immutable digest rollback 운영 경계를 문서화한다.
  - 새 SIGTERM 보장을 추가하지 않고 image rollback과 외부 상태·주문 rollback을 구분한다.
- [x] release/container focused tests, 전체 회귀, lock·lint·diff와 실제 image smoke를 통과한다.
  - 구현 중 승인된 계획의 의미나 범위를 변경해야 하면 즉시 중지하고 사용자 승인을 요청한다.
- [x] 독립 read-only QC 결과를 반영하고 로컬 main 구현 커밋과 최종 상태를 기록한다.
  - PR, tag 생성·push, GHCR 게시, GitHub Release, package visibility 변경, production rollout·restart와 실제 주문은 수행하지 않는다.

### 진행 기록

- 2026-08-19 15:56:31 KST — 승인된 OQ-1~OQ-8과 issue #8의 외부-write 경계를 확인했다.
- 2026-08-19 15:56:31 KST — 별도 구현 worktree나 기능 branch를 만들지 않고 local `main`의 `6fdc42ff077d11ac48c57c08b364c77211d98987`에서 구현·검증을 시작한다.
- 2026-08-19 16:05:45 KST — `uv.lock`, deny-by-default build context, Python 3.12 multi-stage image, branch 검증 workflow와 strict version-tag-only no-rebuild publication workflow를 구현했다.
- 2026-08-19 16:05:45 KST — tag·project·runtime·OCI version 정합, source revision·image ID·archive SHA-256·same-digest selector·secret canary fail-closed helper와 회귀 계약을 구현했다.
- 2026-08-19 16:05:45 KST — release focused `41 passed`, 전체 회귀 `203 passed`, 변경 범위 Ruff, `uv lock --check`, diff check를 통과했다.
- 2026-08-19 16:05:45 KST — 실제 Docker image build, networkless installed import/CLI, isolated RabbitMQ·Redis·fake kis-data-hub startup gate와 initial status, non-root/read-only, archive scan·identity·checksum 검증을 통과했다. strategy decision·core RPC·실제 주문은 실행하지 않았다.
- 2026-08-19 16:05:45 KST — 검증용 local image와 임시 archive를 정리하고 독립 read-only QC를 시작했다.
- 2026-08-19 16:10:23 KST — 최초 독립 QC에서 Blocker 0, Major 1(PEP 517 setuptools 비고정), Minor 1(base/smoke image mutable tag)을 확인해 완료 판정을 보류했다.
- 2026-08-19 16:10:23 KST — setuptools `80.9.0`을 build requirement와 `uv.lock`에 고정하고 lock에서 preinstall한 뒤 build isolation 없이 wheel을 만들며 runtime에서는 제거하도록 보완했다.
- 2026-08-19 16:10:23 KST — uv·Python·RabbitMQ·Redis image 입력을 digest로 고정하고 linux/amd64를 명시했다.
- 2026-08-19 16:10:23 KST — 보완 후 release `41 passed`, 전체 `203 passed`, Ruff·lock·cached diff PASS와 실제 pinned image build·startup smoke·runtime setuptools 부재를 재확인하고 독립 재QC를 시작했다.
- 2026-08-19 16:11:15 KST — 독립 재QC 최종 PASS(Blocker 0 / Major 0 / Minor 0)를 확인했다.
- 2026-08-19 16:11:15 KST — local `main`에 `7b3750781f27f53fa4a35122e49d8fe45b486ffe` (`feat: add verified GHCR publication contract`)를 커밋했고 tracked worktree/index clean을 확인했다.
- 2026-08-19 16:11:15 KST — push, PR, tag 생성·push, GHCR 게시, GitHub Release, package visibility 변경, production rollout·restart와 실제 주문은 수행하지 않았다.

> 각 작업이 실제로 완료되고 검증될 때마다 이 댓글의 체크박스와 진행 기록을 갱신한다. 체크 표시는 구현 완료 또는 해당 검증의 실제 통과를 의미하며, 배포·릴리스 완료를 의미하지 않는다.
