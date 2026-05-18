# syntax=docker/dockerfile:1.7

# ---- builder ---------------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps needed at build time only.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY reviewer ./reviewer

RUN pip install --upgrade pip && pip install --prefix=/install .


# ---- runtime ---------------------------------------------------------------
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SEMGREP_VERSION=1.85.0

# git is required for shallow PR clone; semgrep is the security agent's tool.
# Pin semgrep here (not in pyproject) since it's a CLI not a runtime Python dep.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "semgrep==${SEMGREP_VERSION}"

# Copy the wheel-installed package + entrypoints from the builder.
COPY --from=builder /install /usr/local
COPY reviewer /app/reviewer
COPY pyproject.toml README.md /app/

WORKDIR /app

# Run as non-root.
RUN useradd --uid 1001 --create-home --shell /bin/bash reviewer \
    && chown -R reviewer:reviewer /app
USER reviewer

# CLI by default. Override CMD for HTTP API:
#   docker run reviewer uvicorn reviewer.api:app --host 0.0.0.0 --port 8000
ENTRYPOINT ["reviewer"]
CMD ["--help"]
