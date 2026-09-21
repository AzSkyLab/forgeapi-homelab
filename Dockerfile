FROM hashicorp/terraform:1.15.9 AS terraform
FROM temporalio/temporal:1.8.3 AS temporal

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
COPY --from=terraform /bin/terraform /usr/local/bin/terraform
# Temporal CLI: lets one container run everything (`python -m app.allinone`).
COPY --from=temporal /usr/local/bin/temporal /usr/local/bin/temporal
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
    FORGEAPI_DATA_DIR=/data
# A real user entry: the Temporal binary refuses to start for a bare UID ("$USER set in environment").
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin forgeapi \
    && mkdir -p /data && chown 1000:1000 /data
ENV USER=forgeapi
USER 1000:1000
EXPOSE 8000
# One image, one HTTP app: Temporal dev server + worker + API. docker compose and the lab's
# three-app hosting override this with a single role per container.
CMD ["python", "-m", "app.allinone"]
