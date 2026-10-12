# Tech Stack

[One or two sentences: the shape of the system and where authority lives.]

## Core

| Layer | Choice | Rationale |
|---|---|---|
| Language | [Python 3.12+] | [why] |
| Web framework | [FastAPI] | [why] |
| Orchestration / jobs | [Temporal, queue, none] | [why] |
| Datastore | [SQLite, Postgres, Table Storage] | [why] |
| Infra execution | [Terraform, SDK calls] | [why] |
| Packaging / deploy | [uv, Docker, AKS] | [why] |

## Architecture

- **[Component]** (`app/[module].py`): [responsibility, boundary, what it must never do].
- **[Component]**: [...]
- **Config** lives in [env vars / files]; [which are required, which are per-tenant].

## Conventions

- **API contract:** [OpenAPI source of truth; versioning and compatibility rule].
- **Errors:** [shape, status codes, reason codes].
- **Typing and style:** [ruff rules, line length, type hints].
- **Tests:** [one test file per feature; fixtures; what must be mocked and what must not].
- **Dependencies:** [how new ones are approved].

## Non-negotiables

Rules every phase must hold to. The reviewer checks each one. Quote the source.

- [Authentication and authorization: every mutating route ...]
- [Audit: every change records ... before it runs]
- [Secrets: never stored, logged or returned ...]
- [Safety: nothing changes real infrastructure without ...]
- [Cost: ...]

## Commands

| Command | What it checks |
|---|---|
| `[uv sync]` | Install dependencies |
| `[uv run pytest]` | Test suite |
| `[uv run ruff check .]` | Lint |
| `[uv run ruff format --check .]` | Format |
| `[uv run uvicorn app.main:app --reload]` | Run locally |
| `[integration command]` | [Only with prerequisites X running] |

Mark a command `[unverified]` until someone has run it.

## Platforms and Deployment

- **Development:** [OS, local prerequisites].
- **Hosted:** [where it runs, how it is released].

## What We Are Not Using

- No [technology we rejected, and why in a few words].
- No new dependencies without [who] approval.
