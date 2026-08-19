# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.11.14@sha256:1025398289b62de8269e70c45b91ffa37c373f38118d7da036fb8bb8efc85d97 AS uv
FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a AS build

ARG SOURCE_COMMIT
ARG RELEASE_VERSION
WORKDIR /opt/trex-tvr

RUN test "${#SOURCE_COMMIT}" -eq 40 \
    && case "${SOURCE_COMMIT}" in *[!0-9a-f]*) false ;; *) true ;; esac \
    || { echo "SOURCE_COMMIT must be a 40-character lowercase commit" >&2; exit 1; }
RUN python -c 'import re,sys; assert re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", sys.argv[1])' "${RELEASE_VERSION}"

COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
COPY trex_tvr/ ./trex_tvr/
RUN uv sync --locked --no-dev --no-install-project --extra build \
    && uv sync --locked --no-dev --no-editable --extra build --no-build-isolation \
    && uv pip uninstall --python .venv/bin/python setuptools \
    && .venv/bin/python -c "import importlib.util; assert importlib.util.find_spec('setuptools') is None"

FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a AS runtime
ARG SOURCE_COMMIT
ARG RELEASE_VERSION
LABEL org.opencontainers.image.source="https://github.com/fourblocks2020/trex-tvr-v1" \
      org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      org.opencontainers.image.version="${RELEASE_VERSION}"

RUN test "${#SOURCE_COMMIT}" -eq 40 \
    && case "${SOURCE_COMMIT}" in *[!0-9a-f]*) false ;; *) true ;; esac \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin trex \
    || { echo "SOURCE_COMMIT must be a 40-character lowercase commit" >&2; exit 1; }

WORKDIR /opt/trex-tvr
COPY --from=build /opt/trex-tvr/.venv ./.venv
ENV PATH="/opt/trex-tvr/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 10001:10001
ENTRYPOINT ["trex-tvr"]
