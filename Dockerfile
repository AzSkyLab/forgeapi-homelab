FROM hashicorp/terraform:1.15.9 AS terraform

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
COPY --from=terraform /bin/terraform /usr/local/bin/terraform
# git: Terraform fetches each pattern from its own repo at the pinned commit.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
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
    FORGEAPI_DATA_DIR=/data \
    FORGEAPI_TEMPORAL_ADDRESS=temporal:7233
USER 1000:1000
