# syntax=docker/dockerfile:1

# Build stage - install dependencies
FROM python:3.12-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set environment variables
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (for better caching)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --only-group archiver

# Copy source and install the project
COPY README.md ./
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --only-group archiver && \
    uv pip install --no-deps -e .


# Runtime stage - minimal image
FROM python:3.12-slim AS runtime

# Create non-root user
RUN groupadd --gid 1000 archiver && \
    useradd --uid 1000 --gid 1000 --shell /bin/bash archiver

WORKDIR /app

# Copy virtual environment and source from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

# Set environment variables
ARG APP_VERSION=dev
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_VERSION=${APP_VERSION}

# Copy agencies config (if present, can be overridden via mount)
COPY --chown=archiver:archiver agencies.example.yaml /app/agencies.yaml

# Switch to non-root user
USER archiver

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

# Expose ports
EXPOSE 8080

# Run the application
ENTRYPOINT ["python", "-m", "gtfs_rt_archiver"]
