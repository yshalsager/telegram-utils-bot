FROM public.ecr.aws/docker/library/python:3.14-slim-bookworm

RUN echo 'deb http://deb.debian.org/debian bookworm main non-free contrib' >> /etc/apt/sources.list && apt-get update && apt-get install -y --no-install-recommends \
    # building deps
    gcc \
    python3-dev \
    # for code update
    git \
    # for media conversion
    ffmpeg \
    # for downloading files
    aria2 \
    # for debugging only
    nano \
    # for tafrigh
    mime-support \
    # OCR
    tesseract-ocr \
    tesseract-ocr-ara \
    tesseract-ocr-eng \
    # PDF compression
    ghostscript \
    # for tahweel
    poppler-utils \
    # for tafrigh > auditok > pyaudio \
    portaudio19-dev \
    # 7zip
    p7zip-full \
    p7zip-rar \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN useradd -m appuser && \
    mkdir -p /code && \
    chown -R appuser:appuser /code

USER appuser

WORKDIR /code
ENV PATH="/code/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_CACHE_DIR=/code/.cache/uv \
    UV_COMPILE_BYTECODE=1 \
    UV_FROZEN=1 \
    UV_LINK_MODE=copy \
    UV_NO_MANAGED_PYTHON=1 \
    UV_PROJECT_ENVIRONMENT=/code/.venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_REQUIRE_HASHES=1 \
    UV_VERIFY_HASHES=1 \
    VIRTUAL_ENV=/code/.venv

RUN --mount=type=bind,source=uv.lock,target=/code/uv.lock \
    --mount=type=bind,source=pyproject.toml,target=/code/pyproject.toml \
    uv venv $VIRTUAL_ENV && \
    uv sync --no-install-project --no-editable

WORKDIR /code/app
CMD ["python", "-m", "src"]
