FROM python:3.12-slim-bookworm

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

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/code/.venv/bin:$PATH"

WORKDIR /code/app
RUN uv sync --frozen --no-cache

RUN useradd -m appuser
USER appuser

CMD ["python3", "-m", "src"]
