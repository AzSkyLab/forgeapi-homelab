FROM hashicorp/terraform:1.16.5@sha256:c7926feace05d0f7e73542842bf3945924e955a1f782cf000ccbb8d18fa42d77 AS terraform
FROM temporalio/temporal:1.8.3@sha256:cea463d98a8d6def4420f903ea5c3fcd0d85c8d10fbcc2770a50c12fff2eb26d AS temporal

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58 AS runtime
COPY --from=terraform /bin/terraform /usr/local/bin/terraform
# git: Terraform fetches each pattern from its own repo at the pinned commit.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app app
COPY patterns.yaml ./
COPY examples examples
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    FORGEAPI_DATA_DIR=/data
# The API and Temporal worker share a persistent local data directory owned by this user.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin forgeapi \
    && mkdir -p /data && chown 1000:1000 /data
ENV USER=forgeapi
USER 1000:1000
EXPOSE 8000

# Lab/local only (app/engine.py, compose.yaml): bundles the Temporal dev-server binary and runs
# it plus the worker. Defined before the default stage below so a plain `docker build .` (no
# --target) still builds the production stage, which carries no Temporal server binary.
FROM runtime AS engine
COPY --from=temporal /usr/local/bin/temporal /usr/local/bin/temporal
CMD ["python", "-m", "app.engine"]

# Default/production stage: API and worker against an external Temporal (AGENTS.md, deploy/aks).
FROM runtime
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
