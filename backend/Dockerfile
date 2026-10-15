# API and ingest service image. Build from the repository root:
#   docker build -f backend/Dockerfile -t policy-analytics-api .
# The code keeps the repository layout (/app/backend), so runtime state lands in /app/var: mount a volume there
# to keep the document index, caches, models and conversations between runs.
FROM python:3.12.14-slim-bookworm
# Links the published image to its repository in the GitHub Container Registry.
LABEL org.opencontainers.image.source="https://github.com/garygsw/agentic-policy-analytics" \
      org.opencontainers.image.description="Agentic policy analytics: API and ingest service" \
      org.opencontainers.image.licenses="MIT"

COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

WORKDIR /app/backend
# Dependencies first (cached until the lock file changes), then the code.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev

# Run as an unprivileged user that owns only the state directory.
RUN useradd --create-home --uid 1000 app && mkdir -p /app/var && chown app /app/var
USER app
ENV PATH="/app/backend/.venv/bin:$PATH"

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"]
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
