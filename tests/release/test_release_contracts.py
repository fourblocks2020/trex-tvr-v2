from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "publish-image.yml"
VERIFY_WORKFLOW = REPO / ".github" / "workflows" / "verify-container.yml"
DOCKERFILE = REPO / "Dockerfile"
DOCKERIGNORE = REPO / ".dockerignore"
OPERATIONS = REPO / "docs" / "container-operations.md"


def assert_single_build_and_registry_write_path(text: str) -> None:
    verify_job, publish_job = text.split("\n  publish:", 1)
    assert text.count("docker build ") == 1
    assert "docker buildx" not in text
    assert "docker/build-push-action" not in text
    assert text.count("image_release.py publish") == 1
    for forbidden in (
        "packages: write",
        "REGISTRY_TOKEN",
        "docker login",
        "docker push",
        "docker/login-action",
        "image_release.py publish",
    ):
        assert forbidden not in verify_job
    assert "docker build" not in publish_job


def test_uv_lock_is_versioned_input_and_fresh():
    lock = REPO / "uv.lock"
    assert lock.is_file()
    assert "trex-tvr-v1" in lock.read_text(encoding="utf-8")
    subprocess.run(["uv", "lock", "--check"], cwd=REPO, check=True, capture_output=True, text=True)


def test_workflow_has_only_tag_push_trigger_and_strict_fail_closed_event_gate():
    text = WORKFLOW.read_text(encoding="utf-8")
    trigger = text.split("permissions:", 1)[0]
    assert re.search(r"(?m)^on:\n  push:\n    tags:\n      - \"v\*\.\*\.\*\"$", trigger)
    assert "branches:" not in trigger
    assert "pull_request:" not in trigger
    assert "workflow_dispatch:" not in trigger
    assert "validate-event" in text
    assert "validate-checkout" in text
    assert "validate-versions" in text
    assert "--platform linux/amd64" in text
    assert 'git rev-parse HEAD' in text
    assert "strict version tag push" in text
    assert "ref: ${{ github.sha }}" in text


def test_branch_workflow_verifies_without_registry_write():
    text = VERIFY_WORKFLOW.read_text(encoding="utf-8")
    assert "branches:\n      - main" in text
    assert "pull_request:" in text
    assert "uv sync --extra dev --locked" in text
    assert "uv run pytest -q" in text
    assert text.count("docker build ") == 1
    assert "--platform linux/amd64" in text
    assert "image_release.py smoke" in text
    for forbidden in ("packages: write", "REGISTRY_TOKEN", "docker login", "docker push"):
        assert forbidden not in text


def test_verification_is_locked_and_precedes_the_single_build_and_publication():
    text = WORKFLOW.read_text(encoding="utf-8")
    publish_job = text.split("\n  publish:", 1)[1]
    assert_single_build_and_registry_write_path(text)
    assert "uv sync --extra dev --locked" in text
    assert "uv run pytest -q" in text
    assert "uv run ruff check scripts/image_release.py tests/release" in text
    assert text.index("uv run pytest -q") < text.index("docker build ")
    assert text.index("uv run ruff check") < text.index("docker build ")
    assert "docker login" not in text
    assert "docker push" not in text
    assert "REGISTRY_TOKEN" in publish_job


@pytest.mark.parametrize(
    "mutation",
    [
        "\n      - name: Hidden rebuild\n        run: docker buildx build .\n",
        "\n      - name: Hidden registry login\n        uses: docker/login-action@v3\n",
        "\n      - name: Hidden publish helper\n        run: python scripts/image_release.py publish\n",
    ],
)
def test_workflow_mutations_with_extra_build_or_registry_write_path_are_rejected(mutation):
    text = WORKFLOW.read_text(encoding="utf-8")
    mutated = text.replace("\n  publish:\n", mutation + "\n  publish:\n", 1)
    with pytest.raises(AssertionError):
        assert_single_build_and_registry_write_path(mutated)


def test_workflow_hands_off_image_id_checksum_and_exact_archive_without_rebuild():
    text = WORKFLOW.read_text(encoding="utf-8")
    for expected in (
        "tvr-image.tar",
        "tvr-image.tar.sha256",
        "tvr-image.id",
        "scripts/image_release.py",
        "docker save",
        "record-image-id",
    ):
        assert expected in text
    assert ":latest" not in text


def test_dockerfile_is_python312_locked_allowlist_non_root_and_revision_labeled():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert text.count("FROM python:3.12-slim@sha256:") == 2
    assert "ghcr.io/astral-sh/uv:0.11.14@sha256:" in text
    assert "uv sync --locked --no-dev --no-editable" in text
    assert "--extra build --no-build-isolation" in text
    assert "uv pip uninstall --python .venv/bin/python setuptools" in text
    assert "COPY pyproject.toml uv.lock ./" in text
    assert "COPY trex_tvr/ ./trex_tvr/" in text
    assert "COPY ." not in text
    assert "pip install" not in text
    assert 'org.opencontainers.image.revision="${SOURCE_COMMIT}"' in text
    assert 'org.opencontainers.image.version="${RELEASE_VERSION}"' in text
    assert text.count("ARG RELEASE_VERSION") == 2
    assert "USER 10001:10001" in text
    assert "trex_core" not in text


def test_dockerignore_is_deny_by_default_with_only_build_allowlist():
    lines = [line.strip() for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()]
    assert lines[0] == "**"
    assert [line for line in lines if line.startswith("!")] == [
        "!pyproject.toml",
        "!uv.lock",
        "!trex_tvr/",
        "!trex_tvr/**",
    ]
    for forbidden in (".git", ".proofline", ".env", "config", "artifacts", "tests"):
        assert forbidden in lines


def test_operations_require_digest_pin_mounts_dependencies_and_state_boundary():
    text = OPERATIONS.read_text(encoding="utf-8")
    for required in (
        "@sha256:<64-lowercase-hex-digest>",
        "--user 10001:10001",
        "--read-only",
        "readonly",
        "TREX_TVR_STATUS_PATH=/status/status.json",
        "RabbitMQ",
        "Redis",
        "kis-data-hub",
        "docker stop --time 30",
        "does **not** roll back or reset RabbitMQ messages/state",
        "does **not** add or claim a new SIGTERM lifecycle contract",
        "does not replay an existing Git tag",
    ):
        assert required in text
    assert "@sha256" in text
    assert ":latest" not in text
    assert "trex_core" not in text
