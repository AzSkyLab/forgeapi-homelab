# Phase N — [Name]: Requirements

Status: [Draft | Approved | In progress | Shipped (backfilled YYYY-MM-DD from <sha>)]

## Scope

[Two or three sentences: what changes for the caller or operator.]

### Included

- [Behaviour, written so a test can assert it.]

| Route / operation | Who | Request | Response / effect |
|---|---|---|---|
| `[POST /v1/...]` | [caller kind] | [fields] | [status, body] |

## Decisions

- **[Decision].** [What and why.] *(Backfilled specs: `Evidence: <file>, <sha>`; why unknown: `Why: [unknown]`.)*

## Context

[Existing patterns to follow, neighbouring phases, links to code and docs.]

## Constitution check

One line per non-negotiable in `specs/tech-stack.md`.

- [Rule]: [holds because ... | deviates at ...]

## Failure modes

| Situation | Behaviour |
|---|---|
| Bad input | [status and reason] |
| Partial failure | [what is left behind, how it recovers] |
| Retry / duplicate request | [...] |
| Restart mid-operation | [...] |

## Out of Scope

- [Deferred item] → [Phase N or "unplanned"].

## Known gaps *(backfilled specs)*

- [Missing, stubbed or untested behaviour.]

## Unknowns *(backfilled specs)*

- [Question only a human can answer.]
