# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.14-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.9
# deno runtime, for yt-dlp
ARG DENO_IMAGE=denoland/deno:bin-2.6.0

FROM ${UV_IMAGE} AS uv
FROM ${DENO_IMAGE} AS deno
FROM ${PYTHON_IMAGE} AS builder

ENV DEBIAN_FRONTEND=noninteractive
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    pkg-config \
    portaudio19-dev

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /code
ENV UV_CACHE_DIR=/root/.cache/uv \
    UV_COMPILE_BYTECODE=1 \
    UV_FROZEN=1 \
    UV_LINK_MODE=copy \
    UV_NO_MANAGED_PYTHON=1 \
    UV_PROJECT_ENVIRONMENT=/code/.venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_REQUIRE_HASHES=1 \
    UV_VERIFY_HASHES=1 \
    VIRTUAL_ENV=/code/.venv

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=/code/uv.lock \
    --mount=type=bind,source=pyproject.toml,target=/code/pyproject.toml \
    uv sync --no-install-project --no-dev

FROM ${PYTHON_IMAGE} AS runtime

ARG UID=1000
ARG GID=1000

ENV DEBIAN_FRONTEND=noninteractive
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    echo 'deb http://deb.debian.org/debian bookworm main contrib non-free non-free-firmware' > /etc/apt/sources.list.d/nonfree.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    aria2 \
    ffmpeg \
    git \
    ghostscript \
    libportaudio2 \
    mime-support \
    p7zip-full \
    p7zip-rar \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-ara \
    tesseract-ocr-eng

COPY --from=uv /uv /usr/local/bin/uv

COPY --from=deno /deno /usr/local/bin/deno

RUN groupadd -g ${GID} appuser && useradd -m -u ${UID} -g ${GID} appuser \
    && mkdir -p /code/app \
    && chown -R appuser:appuser /code/app

WORKDIR /code
ENV PATH="/code/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_MANAGED_PYTHON=1 \
    UV_PROJECT_ENVIRONMENT=/code/.venv \
    UV_PYTHON_DOWNLOADS=never \
    VIRTUAL_ENV=/code/.venv

COPY --from=builder /code/.venv /code/.venv
COPY --chown=appuser:appuser src /code/app/src

RUN mkdir -p /code/app/state \
    && chown -R appuser:appuser /code/app/state

USER appuser

WORKDIR /code/app
CMD ["python", "-m", "src"]
