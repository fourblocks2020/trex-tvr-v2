# Container operations

## Identity and publication boundary

A requester-authorized strict `vX.Y.Z` Git tag push selects one exact full source commit. CI verifies and builds that source once, then gives the same tested image two registry selectors:

- `ghcr.io/fourblocks2020/trex-tvr-v1:<source-commit-first-12>`
- `ghcr.io/fourblocks2020/trex-tvr-v1:vX.Y.Z`

Both selectors must report the same registry digest. They are selectors and can be moved by a registry writer; neither is the deployment identity. Record the full source commit, image ID, exported archive SHA-256, both selectors, and the resulting `sha256` digest from the successful workflow evidence. There is no `latest` tag.

Run and roll back only by immutable digest:

```sh
IMAGE='ghcr.io/fourblocks2020/trex-tvr-v1@sha256:<64-lowercase-hex-digest>'
```

Creating or pushing a Git tag, publishing to GHCR, changing package visibility, and pulling/restarting production are separate operator-authorized external writes. Repository implementation or local smoke does not authorize them.

Adding this workflow does not replay an existing Git tag. Do not delete or recreate an immutable release tag to force publication. If the selected version tag already exists, choose a new requester-approved SemVer or use a separately approved, provenance-preserving backfill procedure.

## Runtime contract

The image defaults to UID/GID `10001:10001`. Keep the root filesystem read-only, drop capabilities, prevent privilege escalation, mount the selected TOML read-only, and provide a distinct writable status directory. Supply infrastructure endpoints and secrets at runtime; never bake `.env`, operational TOML, or credentials into an image.

Required external dependencies are RabbitMQ, Redis, and kis-data-hub. Startup fails closed unless RabbitMQ connects, Redis responds to ping, and kis-data-hub `/health` succeeds. The initial `started` status snapshot proves those startup gates only. It does not prove a strategy decision cycle, trex-core RPC, KIS query, intent delivery, account access, order execution, status freshness, or production readiness.

Example (placeholders must be resolved by the operator's secret/config system):

```sh
docker run -d --name trex-tvr-v1 \
  --user 10001:10001 \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,size=32m \
  --mount type=bind,src=/etc/trex/tvr.toml,dst=/config/tvr.toml,readonly \
  --mount type=bind,src=/var/lib/trex-tvr-v1/status,dst=/status \
  --env TREX_TVR_STATUS_PATH=/status/status.json \
  --env TREX_RABBITMQ_URL='amqps://<runtime-secret>@rabbitmq.example.invalid/%2F' \
  --env TREX_REDIS_URL='rediss://<runtime-secret>@redis.example.invalid/0' \
  --env KIS_DATA_HUB_BASE_URL='https://kis-data-hub.example.invalid' \
  --env KIS_DATA_HUB_API_KEY='<runtime-secret>' \
  "${IMAGE}" run --config tvr --config-path /config
```

Prepare the host status directory for UID 10001 and keep the configuration file readable but not writable by that UID. The config and secret provider, RabbitMQ, Redis, and kis-data-hub lifecycle remain external to this image.

## Stop and cleanup boundary

The current daemon guarantees cleanup through its existing Python `finally` path when normal process control reaches it and handles `KeyboardInterrupt`; this release does **not** add or claim a new SIGTERM lifecycle contract. Use the orchestrator's bounded stop timeout, inspect daemon/container logs and the final available status snapshot, then remove the stopped container. Escalation after the timeout may interrupt graceful application cleanup and must be recorded as such.

```sh
docker stop --time 30 trex-tvr-v1
docker inspect trex-tvr-v1 --format '{{.State.Status}} {{.State.ExitCode}}'
docker rm trex-tvr-v1
```

## Digest rollback

1. Select a previously recorded, verified digest and confirm its full source revision and configuration compatibility.
2. Stop and preserve evidence from the current container using the bounded procedure above.
3. Set `IMAGE` to the previous `@sha256:<digest>` reference.
4. Recreate the container with the same read-only config mount, runtime secret injection, writable status mount, non-root/read-only restrictions, and dependency endpoints.
5. Verify the new initial startup snapshot and separately perform any operator-required readiness checks.

Image rollback replaces executable image bytes only. It does **not** roll back or reset RabbitMQ messages/state, Redis state, configuration, broker side effects, intents, orders, or any other external side effect. Those require separate recovery procedures and authorization.
