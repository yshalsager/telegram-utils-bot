# Stage 1: Build dependencies
FROM python:3.13-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=1.8.3 \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN curl -sSL https://install.python-poetry.org | python3 -

ENV PATH="$POETRY_HOME/bin:$PATH"
WORKDIR /code
COPY pyproject.toml poetry.lock* ./
RUN poetry install --with main --no-root

# Stage 2: Run-time image
FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/code/.venv/bin:$PATH"

RUN echo 'deb http://deb.debian.org/debian bookworm main non-free contrib' >> /etc/apt/sources.list && apt-get update && apt-get install -y --no-install-recommends \
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
    tesseract-ocr-all \
    # PDF compression
    ghostscript \
    # for tahweel
    poppler-utils \
    # 7zip
    p7zip-full \
    p7zip-rar \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code
COPY --from=builder /code/.venv ./.venv
RUN useradd -m appuser
USER appuser

WORKDIR /code/app
CMD ["python3", "-m", "src"]
