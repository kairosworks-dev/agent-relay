# syntax=docker/dockerfile:1

FROM python:3.11-slim AS base

# uv's static binary: no need to pip-install uv itself into the image.
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first so code-only changes don't invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

# Application code.
COPY main.py storage.py database.py schemas.py errors.py worker.py dashboard.py dashboard.html ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000

# --host 0.0.0.0: uvicorn defaults to 127.0.0.1, which is unreachable from
# outside the container even with -p published (see Dockerfile note in repo).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
