from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path

import pytest

from scripts import image_release

COMMIT = "a" * 40
IMAGE_ID = "sha256:" + "b" * 64
DIGEST = "sha256:" + "c" * 64
LOCAL_IMAGE = f"tvr-ci:{COMMIT}"


class RecordingRunner:
    def __init__(self, *, revision=COMMIT, version="1.2.3", image_id=IMAGE_ID, digest=DIGEST, fail_on=None):
        self.commands = []
        self.revision = revision
        self.version = version
        self.image_id = image_id
        self.digest = digest
        self.fail_on = fail_on

    def run(self, command, *, input_text=None, timeout=None):
        command = list(command)
        self.commands.append((command, input_text, timeout))
        if self.fail_on and command[: len(self.fail_on)] == list(self.fail_on):
            raise subprocess.CalledProcessError(1, command)
        if command[:2] == ["docker", "load"]:
            return image_release.Result(f"Loaded image: {LOCAL_IMAGE}\n")
        if command[:3] == ["docker", "image", "inspect"] and "{{.Id}}" in command:
            return image_release.Result(self.image_id + "\n")
        if command[:3] == ["docker", "image", "inspect"] and any(
            "image.version" in item for item in command
        ):
            return image_release.Result(self.version + "\n")
        if command[:3] == ["docker", "image", "inspect"]:
            return image_release.Result(self.revision + "\n")
        if command[:2] == ["docker", "push"]:
            return image_release.Result(f"digest: {self.digest} size: 123\n")
        if command[:2] == ["docker", "create"]:
            return image_release.Result("container-id\n")
        return image_release.Result()


def artifacts(tmp_path: Path):
    archive = tmp_path / "tvr-image.tar"
    checksum = tmp_path / "tvr-image.tar.sha256"
    image_id = tmp_path / "tvr-image.id"
    archive.write_bytes(b"tested-image")
    image_release.write_checksum(archive, checksum)
    image_id.write_text(IMAGE_ID + "\n", encoding="ascii")
    return archive, checksum, image_id


def publish(tmp_path: Path, runner: RecordingRunner, **overrides):
    archive, checksum, image_id = artifacts(tmp_path)
    values = {
        "archive": archive,
        "checksum": checksum,
        "image_id": image_id,
        "local_image": LOCAL_IMAGE,
        "commit": COMMIT,
        "event_name": "push",
        "ref": "refs/tags/v1.2.3",
        "actor": "actor",
        "token": "synthetic-token",
        "runner": runner,
    }
    values.update(overrides)
    return image_release.publish_image(**values)


def test_strict_release_event_yields_only_source_and_version_selectors():
    tags = image_release.release_tags("push", "refs/tags/v1.2.3", COMMIT)
    assert tags == (
        f"{image_release.IMAGE}:{COMMIT[:12]}",
        f"{image_release.IMAGE}:v1.2.3",
    )
    assert all(not tag.endswith(":latest") for tag in tags)


@pytest.mark.parametrize(
    ("event", "ref"),
    [
        ("pull_request", "refs/pull/1/merge"),
        ("push", "refs/heads/main"),
        ("push", "refs/heads/release"),
        ("push", "refs/tags/v1.2"),
        ("push", "refs/tags/v1.2.3-rc1"),
        ("push", "refs/tags/v01.2.3"),
        ("workflow_dispatch", "refs/tags/v1.2.3"),
    ],
)
def test_non_strict_or_non_tag_events_are_rejected_before_publication(event, ref):
    with pytest.raises(ValueError, match="strict vX.Y.Z"):
        image_release.release_tags(event, ref, COMMIT)


@pytest.mark.parametrize("commit", ["A" * 40, "a" * 39, "g" * 40, ""])
def test_invalid_exact_source_identity_is_rejected(commit):
    with pytest.raises(ValueError, match="40 lowercase"):
        image_release.release_tags("push", "refs/tags/v1.2.3", commit)


def test_checkout_identity_mismatch_is_rejected():
    assert image_release.validate_checkout_identity(COMMIT, COMMIT) == COMMIT
    with pytest.raises(ValueError, match="checked-out HEAD"):
        image_release.validate_checkout_identity("d" * 40, COMMIT)


def test_tag_project_runtime_versions_must_match(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    runtime = tmp_path / "__init__.py"
    pyproject.write_text('[project]\nname="x"\nversion="1.2.3"\n')
    runtime.write_text('__version__ = "1.2.3"\n')
    assert image_release.validate_source_versions(
        "refs/tags/v1.2.3", pyproject, runtime
    ) == "1.2.3"
    runtime.write_text('__version__ = "1.2.4"\n')
    with pytest.raises(ValueError, match="must match exactly"):
        image_release.validate_source_versions("refs/tags/v1.2.3", pyproject, runtime)


def test_install_smoke_uses_three_non_root_read_only_networkless_probes_and_cleanup():
    runner = RecordingRunner()
    image_release.smoke_installed_image("candidate", runner, timeout=17)
    runs = [call for call in runner.commands if call[0][:2] == ["docker", "run"]]
    removals = [call for call in runner.commands if call[0][:2] == ["docker", "rm"]]
    assert len(runs) == len(removals) == 3
    assert all("--network" in call[0] and "none" in call[0] for call in runs)
    assert all("--read-only" in call[0] and "--cap-drop" in call[0] for call in runs)
    assert [call[0][call[0].index("--entrypoint") + 1] for call in runs] == [
        "python",
        "trex-tvr",
        "trex-tvr-backtest",
    ]


def test_install_smoke_failure_still_removes_container():
    runner = RecordingRunner(fail_on=("docker", "run"))
    with pytest.raises(RuntimeError, match="probe 1 failed"):
        image_release.smoke_installed_image("candidate", runner)
    assert runner.commands[-1][0][:3] == ["docker", "rm", "--force"]


def test_startup_smoke_uses_initial_build_seam_and_cleans_all_resources():
    class StartupRunner(RecordingRunner):
        def run(self, command, *, input_text=None, timeout=None):
            command = list(command)
            if command[:2] == ["docker", "exec"] and command[-2:] == ["cat", "/status/status.json"]:
                self.commands.append((command, input_text, timeout))
                return image_release.Result(
                    '{"last_status":"started","deps":'
                    '{"rabbitmq":"ok","kis_data_hub":"ok","redis":"ok"}}\n'
                )
            if command[:3] == ["docker", "image", "inspect"] and "{{.Config.User}}" in command:
                self.commands.append((command, input_text, timeout))
                return image_release.Result("10001:10001\n")
            return super().run(command, input_text=input_text, timeout=timeout)

    runner = StartupRunner()
    snapshot = image_release.startup_smoke("candidate", runner, timeout=17)
    commands = [call[0] for call in runner.commands]
    app_run = next(
        command
        for command in commands
        if command[:3] == ["docker", "run", "-d"] and "TREX_TVR_STATUS_PATH=/status/status.json" in command
    )
    assert snapshot["last_status"] == "started"
    assert "--read-only" in app_run
    assert app_run[app_run.index("--entrypoint") + 1] == "python"
    assert "build_daemon" in app_run[-1]
    assert "run_forever" not in app_run[-1]
    assert len([command for command in commands if command[:3] == ["docker", "rm", "--force"]]) == 4
    assert any(command[:3] == ["docker", "network", "rm"] for command in commands)


def test_non_object_startup_snapshot_still_cleans_every_resource():
    class NonObjectSnapshotRunner(RecordingRunner):
        def run(self, command, *, input_text=None, timeout=None):
            command = list(command)
            if command[:2] == ["docker", "exec"] and command[-2:] == ["cat", "/status/status.json"]:
                self.commands.append((command, input_text, timeout))
                return image_release.Result("[]\n")
            return super().run(command, input_text=input_text, timeout=timeout)

    runner = NonObjectSnapshotRunner()
    with pytest.raises(RuntimeError, match="isolated startup smoke failed"):
        image_release.startup_smoke("candidate", runner, timeout=17)
    commands = [call[0] for call in runner.commands]
    assert len([command for command in commands if command[:3] == ["docker", "rm", "--force"]]) == 4
    assert any(command[:3] == ["docker", "network", "rm"] for command in commands)


def test_startup_smoke_timeout_cleans_every_container_name_and_network():
    class StartupTimeoutRunner(RecordingRunner):
        def run(self, command, *, input_text=None, timeout=None):
            command = list(command)
            if (
                command[:3] == ["docker", "run", "-d"]
                and "TREX_TVR_STATUS_PATH=/status/status.json" in command
            ):
                self.commands.append((command, input_text, timeout))
                raise subprocess.TimeoutExpired(command, timeout or 0)
            return super().run(command, input_text=input_text, timeout=timeout)

    runner = StartupTimeoutRunner()
    with pytest.raises(RuntimeError, match="isolated startup smoke failed"):
        image_release.startup_smoke("candidate", runner, timeout=17)
    commands = [call[0] for call in runner.commands]
    created_names = {
        command[command.index("--name") + 1]
        for command in commands
        if command[:3] == ["docker", "run", "-d"]
    }
    removed_names = {
        command[3]
        for command in commands
        if command[:3] == ["docker", "rm", "--force"]
    }
    network_name = next(
        command[-1] for command in commands if command[:3] == ["docker", "network", "create"]
    )
    removed_networks = {
        command[-1] for command in commands if command[:3] == ["docker", "network", "rm"]
    }
    assert len(created_names) == 4
    assert removed_names == created_names
    assert removed_networks == {network_name}


def test_startup_smoke_failure_cleans_created_container_and_network():
    class RedisFailureRunner(RecordingRunner):
        def run(self, command, *, input_text=None, timeout=None):
            command = list(command)
            if command[:3] == ["docker", "run", "-d"] and any(
                item.startswith("redis:7.4-alpine@sha256:") for item in command
            ):
                self.commands.append((command, input_text, timeout))
                raise subprocess.CalledProcessError(1, command)
            return super().run(command, input_text=input_text, timeout=timeout)

    runner = RedisFailureRunner()
    with pytest.raises(RuntimeError, match="isolated startup smoke failed"):
        image_release.startup_smoke("candidate", runner, timeout=17)
    commands = [call[0] for call in runner.commands]
    assert any(command[:3] == ["docker", "rm", "--force"] for command in commands)
    assert any(command[:3] == ["docker", "network", "rm"] for command in commands)


def test_corrupt_archive_stops_before_load_login_or_push(tmp_path):
    archive, checksum, image_id = artifacts(tmp_path)
    archive.write_bytes(b"corrupt")
    runner = RecordingRunner()
    with pytest.raises(ValueError, match="checksum mismatch"):
        image_release.publish_image(
            archive=archive,
            checksum=checksum,
            image_id=image_id,
            local_image=LOCAL_IMAGE,
            commit=COMMIT,
            event_name="push",
            ref="refs/tags/v1.2.3",
            actor="actor",
            token="synthetic-token",
            runner=runner,
        )
    assert runner.commands == []


def test_image_id_mismatch_stops_before_tag_login_or_push(tmp_path):
    runner = RecordingRunner(image_id="sha256:" + "d" * 64)
    with pytest.raises(ValueError, match="image ID"):
        publish(tmp_path, runner)
    assert not any(call[0][:2] in (["docker", "tag"], ["docker", "login"], ["docker", "push"]) for call in runner.commands)


def test_revision_mismatch_stops_before_tag_login_or_push(tmp_path):
    runner = RecordingRunner(revision="d" * 40)
    with pytest.raises(ValueError, match="tagged source"):
        publish(tmp_path, runner)
    assert not any(call[0][:2] in (["docker", "tag"], ["docker", "login"], ["docker", "push"]) for call in runner.commands)


def test_valid_no_rebuild_publish_records_complete_evidence(tmp_path):
    runner = RecordingRunner()
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    digest = publish(tmp_path, runner, output_path=output, summary_path=summary)
    commands = [call[0][:2] for call in runner.commands]
    assert digest == DIGEST
    assert ["docker", "build"] not in commands
    assert commands.count(["docker", "push"]) == 2
    assert commands.index(["docker", "load"]) < commands.index(["docker", "login"])
    evidence = output.read_text()
    for value in (COMMIT, IMAGE_ID, DIGEST, COMMIT[:12], "v1.2.3", "archive_sha256="):
        assert value in evidence
    assert ":latest" not in evidence + summary.read_text()


def test_post_push_digest_failure_emits_no_success_evidence(tmp_path):
    runner = RecordingRunner(digest="")
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    with pytest.raises(ValueError, match="valid digest"):
        publish(tmp_path, runner, output_path=output, summary_path=summary)
    assert any(call[0][:2] == ["docker", "push"] for call in runner.commands)
    assert not output.exists()
    assert not summary.exists()


def test_selector_digest_mismatch_emits_no_success_evidence(tmp_path):
    class DifferentDigestRunner(RecordingRunner):
        def run(self, command, *, input_text=None, timeout=None):
            result = super().run(command, input_text=input_text, timeout=timeout)
            pushes = [call for call in self.commands if call[0][:2] == ["docker", "push"]]
            if list(command)[:2] == ["docker", "push"] and len(pushes) == 2:
                return image_release.Result("digest: sha256:" + "e" * 64 + " size: 123\n")
            return result

    output = tmp_path / "output"
    with pytest.raises(ValueError, match="different registry digests"):
        publish(tmp_path, DifferentDigestRunner(), output_path=output)
    assert not output.exists()


@pytest.mark.parametrize("member_kind", ["payload", "symlink", "hardlink", "pax"])
def test_canary_scan_finds_payload_and_archive_metadata(member_kind):
    nested = io.BytesIO()
    with tarfile.open(fileobj=nested, mode="w", format=tarfile.PAX_FORMAT) as archive:
        member = tarfile.TarInfo("layer/item")
        if member_kind == "payload":
            payload = image_release.CANARY
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        elif member_kind in {"symlink", "hardlink"}:
            member.type = tarfile.SYMTYPE if member_kind == "symlink" else tarfile.LNKTYPE
            member.linkname = image_release.CANARY.decode()
            archive.addfile(member)
        else:
            member.pax_headers = {"canary-key": image_release.CANARY.decode()}
            archive.addfile(member, io.BytesIO(b""))
    outer = io.BytesIO()
    with tarfile.open(fileobj=outer, mode="w") as archive:
        member = tarfile.TarInfo("layer.tar")
        member.size = len(nested.getvalue())
        archive.addfile(member, io.BytesIO(nested.getvalue()))
    with pytest.raises(ValueError, match="canary marker"):
        image_release._scan_tar_once(outer.getvalue(), scan_nested_payloads=True)
