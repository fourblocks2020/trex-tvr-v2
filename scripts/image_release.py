#!/usr/bin/env python3
"""Fail-closed helpers for building, testing, handing off, and publishing one image."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

IMAGE = "ghcr.io/fourblocks2020/trex-tvr-v1"
CANARY = b"TREX_TVR_SECRET_CANARY_DO_NOT_SHIP"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_REF_RE = re.compile(r"^refs/tags/v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
DIGEST_RE = re.compile(r"(?:^|\s)digest:\s*(sha256:[0-9a-f]{64})(?:\s|$)")


@dataclass(frozen=True)
class Result:
    stdout: str = ""
    stderr: str = ""


class Runner(Protocol):
    def run(
        self,
        command: Sequence[str],
        *,
        input_text: str | None = None,
        timeout: int | None = None,
    ) -> Result: ...


class SubprocessRunner:
    def run(
        self,
        command: Sequence[str],
        *,
        input_text: str | None = None,
        timeout: int | None = None,
    ) -> Result:
        completed = subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            check=True,
            timeout=timeout,
        )
        return Result(completed.stdout, completed.stderr)


def validate_commit(commit: str) -> str:
    if not COMMIT_RE.fullmatch(commit):
        raise ValueError("source commit must be 40 lowercase hexadecimal characters")
    return commit


def validate_checkout_identity(actual: str, expected: str) -> str:
    validate_commit(actual)
    validate_commit(expected)
    if actual != expected:
        raise ValueError("checked-out HEAD does not match the tagged source commit")
    return actual


def validate_release_event(event_name: str, ref: str, commit: str) -> str:
    validate_commit(commit)
    if event_name != "push" or not VERSION_REF_RE.fullmatch(ref):
        raise ValueError("publication requires a push of a strict vX.Y.Z tag")
    return ref.removeprefix("refs/tags/")


def release_tags(event_name: str, ref: str, commit: str) -> tuple[str, str]:
    version = validate_release_event(event_name, ref, commit)
    return f"{IMAGE}:{commit[:12]}", f"{IMAGE}:{version}"


def release_version(ref: str) -> str:
    if VERSION_REF_RE.fullmatch(ref) is None:
        raise ValueError("release version requires a strict vX.Y.Z tag ref")
    return ref.removeprefix("refs/tags/v")


def validate_source_versions(ref: str, pyproject: Path, runtime_init: Path) -> str:
    version = release_version(ref)
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    runtime_text = runtime_init.read_text(encoding="utf-8")
    match = re.search(
        r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', runtime_text, re.MULTILINE
    )
    if match is None:
        raise ValueError("runtime __version__ is missing")
    if project != version or match.group(1) != version:
        raise ValueError("tag, project version, and runtime version must match exactly")
    return version


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksum(archive: Path, output: Path) -> str:
    checksum = sha256_file(archive)
    output.write_text(f"{checksum}  {archive.name}\n", encoding="ascii")
    return checksum


def verify_checksum(archive: Path, checksum_path: Path) -> str:
    if not archive.is_file() or not checksum_path.is_file():
        raise ValueError("image archive or checksum evidence is missing")
    fields = checksum_path.read_text(encoding="ascii").strip().split()
    if (
        len(fields) != 2
        or fields[1] != archive.name
        or not re.fullmatch(r"[0-9a-f]{64}", fields[0])
    ):
        raise ValueError("checksum evidence has an invalid format")
    if sha256_file(archive) != fields[0]:
        raise ValueError("image archive checksum mismatch")
    return fields[0]


def validate_image_id(image_id: str) -> str:
    if not IMAGE_ID_RE.fullmatch(image_id):
        raise ValueError("image ID must be sha256 followed by 64 lowercase hexadecimal characters")
    return image_id


def inspect_value(image: str, template: str, runner: Runner) -> str:
    return runner.run(["docker", "image", "inspect", "--format", template, image]).stdout.strip()


def inspect_image_id(image: str, runner: Runner) -> str:
    return validate_image_id(inspect_value(image, "{{.Id}}", runner))


def inspect_revision(image: str, runner: Runner) -> str:
    return inspect_value(
        image,
        '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
        runner,
    )


def inspect_version(image: str, runner: Runner) -> str:
    return inspect_value(
        image,
        '{{ index .Config.Labels "org.opencontainers.image.version" }}',
        runner,
    )


def write_image_id(image: str, output: Path, runner: Runner) -> str:
    image_id = inspect_image_id(image, runner)
    output.write_text(image_id + "\n", encoding="ascii")
    return image_id


def read_image_id(path: Path) -> str:
    text = path.read_text(encoding="ascii")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}\n?", text):
        raise ValueError("tested image ID evidence has an invalid format")
    return text.rstrip("\n")


def _clean(result: Result) -> None:
    if CANARY in (result.stdout + result.stderr).encode():
        raise ValueError("canary marker found on a scanned surface")


def _remove(name: str, runner: Runner, timeout: int) -> None:
    _clean(runner.run(["docker", "rm", "--force", name], timeout=timeout))


def _docker_error_text(error: subprocess.CalledProcessError) -> str:
    parts = (error.stdout, error.stderr)
    return "\n".join(
        part.decode(errors="replace") if isinstance(part, bytes) else str(part or "")
        for part in parts
    )


def _remove_if_present(name: str, runner: Runner, timeout: int) -> None:
    try:
        _remove(name, runner, timeout)
    except subprocess.CalledProcessError as error:
        if "no such container" not in _docker_error_text(error).lower():
            raise


def _remove_network_if_present(name: str, runner: Runner, timeout: int) -> None:
    try:
        _clean(runner.run(["docker", "network", "rm", name], timeout=timeout))
    except subprocess.CalledProcessError as error:
        text = _docker_error_text(error).lower()
        if "not found" not in text and "no such network" not in text:
            raise


def smoke_installed_image(image: str, runner: Runner, timeout: int = 30) -> None:
    probes = (
        ("python", "-I", "-c", "import trex_tvr"),
        ("trex-tvr", "--help"),
        ("trex-tvr-backtest", "--help"),
    )
    for index, probe in enumerate(probes):
        name = f"tvr-install-smoke-{os.getpid()}-{index}"
        command = [
            "docker", "run", "--name", name, "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "64",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m", "--entrypoint", probe[0], image,
            *probe[1:],
        ]
        failure: Exception | None = None
        try:
            _clean(runner.run(command, timeout=timeout))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            failure = error
        try:
            _remove(name, runner, timeout)
        except (
            OSError, ValueError, RuntimeError, subprocess.SubprocessError
        ) as cleanup_error:
            if failure is None:
                raise RuntimeError("install smoke cleanup failed") from cleanup_error
        if failure is not None:
            raise RuntimeError(f"install smoke probe {index + 1} failed") from failure


def _docker_wait_ready(
    command: Sequence[str], runner: Runner, deadline: float, description: str
) -> None:
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            runner.run(command, timeout=5)
            return
        except (subprocess.SubprocessError, OSError) as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError(f"timed out waiting for {description}") from last_error


def startup_smoke(image: str, runner: Runner, timeout: int = 90) -> dict:
    """Exercise only dependency startup and the initial status snapshot."""
    suffix = f"{os.getpid()}-{time.time_ns()}"
    network = f"tvr-smoke-{suffix}"
    rabbit = f"tvr-rabbit-{suffix}"
    redis = f"tvr-redis-{suffix}"
    kdh = f"tvr-kdh-{suffix}"
    app = f"tvr-app-{suffix}"
    cleanup_names = [rabbit, redis, kdh, app]
    failure: BaseException | None = None
    snapshot: dict = {}
    with tempfile.TemporaryDirectory(prefix="tvr-smoke-") as directory:
        root = Path(directory)
        config = root / "smoke.toml"
        status_dir = root / "status"
        status_dir.mkdir(mode=0o777)
        status_dir.chmod(0o777)
        config.write_text(
            'instance_key = "smoke"\ninstances = ["smoke"]\npoll_interval = 3600\n'
            'rpc_timeout_seconds = 1\nworker_count = 1\nsession_start = "00:00"\n'
            'session_end = "23:59"\n[[schedule]]\nstart = "00:00"\nend = "23:59"\n'
            '[strategy.tvr_v1]\ntarget_weight = 0.5\nband = 0.05\nmin_trade_krw = 10000\n',
            encoding="utf-8",
        )
        config.chmod(0o444)
        deadline = time.monotonic() + timeout
        mock_server = (
            "from http.server import BaseHTTPRequestHandler,HTTPServer;"
            "H=type('H',(BaseHTTPRequestHandler,),{"
            "'do_GET':lambda s:(s.send_response(200),s.end_headers(),s.wfile.write(b'{}')),"
            "'log_message':lambda *a:None});HTTPServer(('0.0.0.0',8080),H).serve_forever()"
        )
        startup_probe = (
            "import time; "
            "from trex_tvr.runtime.config import load_config; "
            "from trex_tvr.runtime.daemon import build_daemon; "
            "daemon,shutdown=build_daemon(load_config('/config/smoke.toml')); "
            "time.sleep(3600)"
        )
        try:
            runner.run(["docker", "network", "create", "--internal", network], timeout=10)
            runner.run([
                "docker", "run", "-d", "--name", rabbit, "--network", network,
                "--network-alias", "rabbitmq", "-e", "RABBITMQ_DEFAULT_USER=smoke",
                "-e", "RABBITMQ_DEFAULT_PASS=synthetic-only",
                "rabbitmq:3.13-alpine@sha256:d7af1c87c5f1eda13fcfca06db452bf3aeab6619fc3358b68535c0c02c4e52bc",
            ], timeout=20)
            runner.run([
                "docker", "run", "-d", "--name", redis, "--network", network,
                "--network-alias", "redis",
                "redis:7.4-alpine@sha256:e7723ff73d963f5cc6d9c4643ea3d989527a402a319239054e9472a7fb9219a2",
            ], timeout=20)
            runner.run([
                "docker", "run", "-d", "--name", kdh, "--network", network,
                "--network-alias", "kdh", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,noexec,nosuid,size=8m",
                "--entrypoint", "python", image, "-c", mock_server,
            ], timeout=20)
            _docker_wait_ready(
                [
                    "docker", "run", "--rm", "--network", network,
                    "--entrypoint", "python", image, "-c",
                    (
                        "import pika; c=pika.BlockingConnection(pika.URLParameters("
                        "'amqp://smoke:synthetic-only@rabbitmq:5672/%2F')); "
                        "c.channel().exchange_declare(exchange='trex.events', "
                        "exchange_type='topic', durable=True); c.close()"
                    ),
                ],
                runner, deadline, "RabbitMQ AMQP login",
            )
            _docker_wait_ready(
                ["docker", "exec", redis, "redis-cli", "ping"], runner, deadline, "Redis"
            )
            runner.run([
                "docker", "run", "-d", "--name", app, "--network", network,
                "--read-only", "--user", "10001:10001", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "128",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
                "--mount", f"type=bind,src={config},dst=/config/smoke.toml,readonly",
                "--mount", f"type=bind,src={status_dir},dst=/status",
                "-e", "TREX_RABBITMQ_URL=amqp://smoke:synthetic-only@rabbitmq:5672/%2F",
                "-e", "TREX_REDIS_URL=redis://redis:6379/0",
                "-e", "KIS_DATA_HUB_BASE_URL=http://kdh:8080",
                "-e", "KIS_DATA_HUB_API_KEY=synthetic-only",
                "-e", "TREX_TVR_STATUS_PATH=/status/status.json",
                "--entrypoint", "python", image, "-c", startup_probe,
            ], timeout=20)
            while time.monotonic() < deadline:
                try:
                    status_result = runner.run(
                        ["docker", "exec", app, "cat", "/status/status.json"], timeout=5
                    )
                    _clean(status_result)
                    parsed = json.loads(status_result.stdout)
                    if not isinstance(parsed, dict):
                        raise TypeError("initial startup snapshot must be a JSON object")
                    snapshot = parsed
                    if snapshot.get("last_status") == "started":
                        break
                except (OSError, TypeError, ValueError, subprocess.SubprocessError):
                    pass
                inspected = runner.run([
                    "docker", "inspect", "--format", "{{.State.Running}}", app
                ], timeout=5).stdout.strip()
                if inspected != "true":
                    raise RuntimeError("daemon exited before initial startup snapshot")
                time.sleep(0.5)
            expected = {
                "last_status": "started",
                "deps": {"rabbitmq": "ok", "kis_data_hub": "ok", "redis": "ok"},
            }
            if snapshot.get("last_status") != expected["last_status"] or snapshot.get("deps") != expected["deps"]:
                raise RuntimeError("initial startup snapshot did not match the required contract")
            user = inspect_value(image, "{{.Config.User}}", runner)
            if not user or user in {"0", "root", "0:0", "root:root"}:
                raise RuntimeError("image default user must be non-root")
        except BaseException as error:  # noqa: BLE001 - cleanup must run for every exit path
            failure = error
        diagnostics = ""
        if failure is not None:
            try:
                logs = runner.run(["docker", "logs", app], timeout=10)
                _clean(logs)
                diagnostics = (logs.stdout + logs.stderr).strip()[-2000:]
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                diagnostics = "daemon logs unavailable"
        cleanup_errors: list[BaseException] = []
        for name in reversed(cleanup_names):
            try:
                _remove_if_present(name, runner, 15)
            except BaseException as error:  # noqa: BLE001 - report every cleanup failure
                cleanup_errors.append(error)
        try:
            _remove_network_if_present(network, runner, 15)
        except BaseException as error:  # noqa: BLE001 - report every cleanup failure
            cleanup_errors.append(error)
        if cleanup_errors:
            raise RuntimeError("startup smoke cleanup failed") from cleanup_errors[0]
        if failure is not None:
            if isinstance(failure, (KeyboardInterrupt, SystemExit)):
                raise failure
            detail = f": {diagnostics}" if diagnostics else ""
            raise RuntimeError(f"isolated startup smoke failed{detail}") from failure
    return snapshot


def _scan_bytes(data: bytes) -> None:
    if CANARY in data:
        raise ValueError("canary marker found on a scanned surface")


def _scan_tar_metadata(member: tarfile.TarInfo) -> None:
    values = [member.name, member.linkname, member.uname, member.gname]
    for key, value in member.pax_headers.items():
        values.extend((key, value))
    _scan_bytes("\n".join(str(value) for value in values).encode(errors="replace"))


def _scan_tar_once(data: bytes, *, scan_nested_payloads: bool) -> None:
    _scan_bytes(data)
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            for member in archive:
                _scan_tar_metadata(member)
                if not member.isfile():
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                payload = extracted.read()
                _scan_bytes(payload)
                if scan_nested_payloads:
                    _scan_tar_once(payload, scan_nested_payloads=False)
    except (tarfile.TarError, OSError, EOFError):
        return


def scan_image(image: str, saved_archive: Path, runner: Runner, timeout: int = 90) -> None:
    for command in (["docker", "history", "--no-trunc", image], ["docker", "image", "inspect", image]):
        _clean(runner.run(command, timeout=timeout))
    container = runner.run(["docker", "create", "--network", "none", image], timeout=timeout)
    container_id = container.stdout.strip()
    if not container_id:
        raise RuntimeError("docker create returned no container ID")
    try:
        _clean(container)
        with tempfile.TemporaryDirectory() as directory:
            filesystem = Path(directory) / "filesystem.tar"
            _clean(runner.run([
                "docker", "export", "--output", str(filesystem), container_id
            ], timeout=timeout))
            _scan_tar_once(filesystem.read_bytes(), scan_nested_payloads=False)
    finally:
        _remove(container_id, runner, timeout)
    _scan_tar_once(saved_archive.read_bytes(), scan_nested_payloads=True)


def assert_clean_output(paths: Sequence[Path]) -> None:
    for path in paths:
        _scan_bytes(path.read_bytes())


def _push_digest(output: str) -> str:
    digests = set(DIGEST_RE.findall(output))
    if len(digests) != 1:
        raise ValueError("registry push did not return exactly one valid digest")
    return digests.pop()


def _append(path: Path | None, text: str) -> None:
    if path is not None:
        with path.open("a", encoding="utf-8") as destination:
            destination.write(text)


def publish_image(
    *, archive: Path, checksum: Path, image_id: Path, local_image: str, commit: str,
    event_name: str, ref: str, actor: str, token: str, runner: Runner,
    output_path: Path | None = None, summary_path: Path | None = None,
) -> str:
    tags = release_tags(event_name, ref, commit)
    archive_sha = verify_checksum(archive, checksum)
    expected_id = read_image_id(image_id)
    loaded = runner.run(["docker", "load", "--input", str(archive)])
    lines = {line.strip() for line in (loaded.stdout + "\n" + loaded.stderr).splitlines()}
    if f"Loaded image: {local_image}" not in lines:
        raise ValueError("docker load did not report the expected local image")
    actual_id = inspect_image_id(local_image, runner)
    if actual_id != expected_id:
        raise ValueError("loaded image ID does not match tested image ID")
    if inspect_revision(local_image, runner) != commit:
        raise ValueError("loaded image revision does not match tagged source commit")
    if inspect_version(local_image, runner) != release_version(ref):
        raise ValueError("loaded image version does not match strict release tag")
    if not actor or not token:
        raise ValueError("registry credentials are missing")
    for tag in tags:
        runner.run(["docker", "tag", local_image, tag])
    runner.run(
        ["docker", "login", "ghcr.io", "--username", actor, "--password-stdin"],
        input_text=token,
    )
    digests = [
        _push_digest((result := runner.run(["docker", "push", tag])).stdout + "\n" + result.stderr)
        for tag in tags
    ]
    if len(set(digests)) != 1:
        raise ValueError("published selectors returned different registry digests")
    digest = digests[0]
    immutable = f"{IMAGE}@{digest}"
    evidence = (
        f"image={immutable}\ndigest={digest}\nsource_commit={commit}\nimage_id={actual_id}\n"
        f"archive_sha256={archive_sha}\nsource_selector={tags[0]}\nversion_selector={tags[1]}\n"
    )
    _append(output_path, evidence)
    _append(summary_path, "## Published trex-tvr-v1 image\n\n" + "".join(
        f"- {line.replace('=', ': `', 1)}`\n" for line in evidence.strip().splitlines()
    ))
    return digest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    event = commands.add_parser("validate-event")
    event.add_argument("--event", required=True)
    event.add_argument("--ref", required=True)
    event.add_argument("--commit", required=True)
    checkout = commands.add_parser("validate-checkout")
    checkout.add_argument("--actual", required=True)
    checkout.add_argument("--expected", required=True)
    versions = commands.add_parser("validate-versions")
    versions.add_argument("--ref", required=True)
    versions.add_argument("--pyproject", type=Path, required=True)
    versions.add_argument("--runtime-init", type=Path, required=True)
    smoke = commands.add_parser("smoke")
    smoke.add_argument("--image", required=True)
    smoke.add_argument("--timeout", type=int, default=90)
    scan = commands.add_parser("scan")
    scan.add_argument("--image", required=True)
    scan.add_argument("--archive", type=Path, required=True)
    checksum = commands.add_parser("checksum")
    checksum.add_argument("--archive", type=Path, required=True)
    checksum.add_argument("--output", type=Path, required=True)
    record = commands.add_parser("record-image-id")
    record.add_argument("--image", required=True)
    record.add_argument("--output", type=Path, required=True)
    clean = commands.add_parser("assert-clean-output")
    clean.add_argument("paths", nargs="+", type=Path)
    publish = commands.add_parser("publish")
    publish.add_argument("--archive", type=Path, required=True)
    publish.add_argument("--checksum", type=Path, required=True)
    publish.add_argument("--image-id", type=Path, required=True)
    publish.add_argument("--local-image", required=True)
    publish.add_argument("--commit", required=True)
    publish.add_argument("--event", required=True)
    publish.add_argument("--ref", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runner = SubprocessRunner()
    if args.command == "validate-event":
        validate_release_event(args.event, args.ref, args.commit)
    elif args.command == "validate-checkout":
        validate_checkout_identity(args.actual, args.expected)
    elif args.command == "validate-versions":
        validate_source_versions(args.ref, args.pyproject, args.runtime_init)
    elif args.command == "smoke":
        smoke_installed_image(args.image, runner, args.timeout)
        startup_smoke(args.image, runner, args.timeout)
    elif args.command == "scan":
        scan_image(args.image, args.archive, runner)
    elif args.command == "checksum":
        write_checksum(args.archive, args.output)
    elif args.command == "record-image-id":
        write_image_id(args.image, args.output, runner)
    elif args.command == "assert-clean-output":
        assert_clean_output(args.paths)
    elif args.command == "publish":
        publish_image(
            archive=args.archive, checksum=args.checksum, image_id=args.image_id,
            local_image=args.local_image, commit=args.commit, event_name=args.event, ref=args.ref,
            actor=os.environ.get("GITHUB_ACTOR", ""), token=os.environ.get("REGISTRY_TOKEN", ""),
            runner=runner,
            output_path=Path(path) if (path := os.environ.get("GITHUB_OUTPUT")) else None,
            summary_path=Path(path) if (path := os.environ.get("GITHUB_STEP_SUMMARY")) else None,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"image release failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
