# GitHub 구현 완료 comment snapshot

- Source: https://github.com/fourblocks2020/trex-tvr-v1/issues/8#issuecomment-5338849508
- Repository: `fourblocks2020/trex-tvr-v1`
- Issue: `#8`
- Captured from live GitHub comment after read-back verification on 2026-08-19.
- Classification: repository-local evidence snapshot; not a canonical ProofLine lifecycle artifact.

---

## 구현 완료

### 구현 내용

- Python 3.12와 repository-owned `uv.lock`을 canonical dependency authority로 사용하도록 구성했다.
- `setuptools==80.9.0`을 lock에 포함하고 build isolation 없이 고정된 build backend를 사용한 뒤 runtime image에서는 제거하도록 구성했다.
- digest-pinned uv·Python base image와 RabbitMQ·Redis smoke image를 사용하고 `linux/amd64`를 명시했다.
- deny-by-default `.dockerignore`, non-root `10001:10001`, read-only root filesystem과 config·secret·status mount 분리 계약을 구현했다.
- branch/main/PR에서는 registry write 없이 검증하고 strict stable `vX.Y.Z` tag push에서만 GHCR publication을 수행하도록 workflow를 분리했다.
- verify job에서 source image를 한 번만 build하고, installed payload·dependency startup·initial status·secret canary를 검증한 exact archive를 publish job에 전달하며 rebuild를 금지했다.
- Git tag·project·runtime·OCI version exact match, source revision, image ID, archive SHA-256, SHA/version selector same-digest와 immutable digest evidence를 fail-closed로 검증하도록 구현했다.
- immutable digest 실행·rollback, 기존 tag 비재생, bounded stop과 외부 RabbitMQ·Redis·broker/order state 비rollback 경계를 운영 문서에 기록했다.

### 실제 검증 결과

- release/container focused: **41 passed**
- 전체 회귀: **203 passed**
- 변경 범위 Ruff: **PASS**
- `uv lock --check`: **PASS**
- staged/unstaged diff check: **PASS**
- digest-pinned 실제 Docker image build: **PASS**
- networkless installed `trex_tvr` import, `trex-tvr --help`, `trex-tvr-backtest --help`: **PASS**
- isolated RabbitMQ·Redis·fake kis-data-hub startup gate와 initial status snapshot: **PASS**
- runtime setuptools 부재, non-root user `10001:10001`, OCI revision/version: **PASS**
- image/archive/metadata/helper-output secret canary scan, image ID와 archive SHA-256 evidence: **PASS**
- 독립 재QC: **PASS — Blocker 0 / Major 0 / Minor 0**

### 비차단 후속 권고

- requester가 실제 release version을 별도로 결정한 뒤 tag 생성·push와 GHCR publication을 승인한다.
- 이미 존재하는 tag는 삭제·재생성하지 않고 새 SemVer 또는 별도 승인된 provenance-preserving backfill 절차를 사용한다.
- publication 성공 뒤 GHCR package visibility와 두 selector의 live same-digest, immutable digest를 별도 확인한다.
- production rollout 전 실제 config·credential·dependency readiness, status freshness와 strategy/execution readiness를 별도로 검증한다.

### 완료 및 외부 반영 경계

- 로컬 `main` 구현·테스트·실제 container smoke·독립 QC·커밋 완료:
  - `7b3750781f27f53fa4a35122e49d8fe45b486ffe`
  - `feat: add verified GHCR publication contract`
- tracked worktree/index: clean
- 원격 push: **수행하지 않음**
- PR: **생성하지 않음**
- Git tag 생성·push: **수행하지 않음**
- GHCR image 게시 및 package visibility 변경: **수행하지 않음**
- GitHub Release: **생성하지 않음**
- production rollout·restart: **수행하지 않음**
- 실제 운영 credential·dependency 접근 및 실제 주문: **수행하지 않음**

따라서 이 완료는 로컬 제품 구현·검증·커밋 완료를 의미하며 image publication, release 또는 운영 배포 완료를 의미하지 않는다.
