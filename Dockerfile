FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm

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

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/
RUN useradd -m appuser
USER appuser

WORKDIR /code
COPY pyproject.toml uv.lock /code/
RUN uv sync --frozen --no-cache
ENV PATH="/code/.venv/bin:$PATH"

WORKDIR /code/app
CMD ["python", "-m", "src"]
