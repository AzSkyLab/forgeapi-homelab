---
name: constitution
description: Bootstraps or updates the three constitution files (specs/mission.md, specs/tech-stack.md, specs/roadmap.md) for a project. Use when adopting spec-driven development in an existing repo, when the user invokes /constitution, or says "set up the specs", "write the mission", "write the tech stack". Reads the repo first and drafts from evidence; interviews only for what the code cannot tell.
model: opus
effort: high
---

# Constitution

The constitution is the small set of files every later spec is checked against:

| File | Holds |
|---|---|
| `specs/mission.md` | What the product is for, who uses it, principles, what it is **not** |
| `specs/tech-stack.md` | Stack, architecture, conventions, non-negotiables, how to test |
| `specs/roadmap.md` | Small phases in order, ticked as they ship (filled by `/backfill-spec` and `/feature-spec`) |

No code is written or changed in this skill.

## Workflow

### 1. Check what exists

If `specs/` already has these files, read them and treat this as an **update**:
propose edits, never overwrite. Templates live in `specs/templates/`; use them for structure.

### 2. Gather evidence (read-only)

Read, in this order, whatever exists: `README.md`, `AGENTS.md` / `CLAUDE.md`,
`docs/` index and any architecture or progress notes, `pyproject.toml`,
`Makefile` / task runner, CI workflows, Dockerfile / compose, the top-level
package layout, the tests layout, and `git log --oneline | head -50`.

Collect **facts only**: language and version, frameworks, datastores,
orchestration, auth, how to run, how to test, how to lint, lint config, the
rules the repo already states ("never X", "always Y").

### 3. Draft `specs/tech-stack.md` from evidence

Fill every section of the template from what you found. In the **Commands**
table put the real commands; run each cheap one (`--help`, collect-only, lint)
to confirm it works, and mark the rest `[unverified]`. Put every rule the repo
already states under **Non-negotiables**, quoting its source (`AGENTS.md`,
`docs/…`). Anything you could not establish becomes `[unknown: <question>]`,
never a guess.

### 4. Interview, one call, BEFORE writing mission.md

Use `AskUserQuestion` with exactly **3 questions in one call**:

| Header | Focus |
|---|---|
| **Purpose** | Who the product is for and the problem it solves, in one sentence (offer 2-3 readings of the repo as options) |
| **Principles** | The 3-6 rules that decide trade-offs (e.g. safe by default, explicit approval before changes, small blast radius) and the non-goals |
| **Success** | What success looks like in 6-12 months and what must never happen |

Options come from your evidence, recommended one first. "You pick" is a valid
answer; record what you picked as a decision the user can change.

### 5. Write the files

- `specs/mission.md` from the answers. Keep it under about 100 lines.
- `specs/tech-stack.md` as drafted, with the user's corrections.
- `specs/roadmap.md`: if it does not exist, create the header and an empty
  **Later phases** paragraph. Shipped work is added by `/backfill-spec`; the
  next phase by `/feature-spec`.

### 6. Wire up the project instructions

Make sure `CLAUDE.md` (or `AGENTS.md`, whichever the repo uses) contains the
spec-driven section and imports `@specs/mission.md` and `@specs/tech-stack.md`.
The text is in `specs/templates/CLAUDE.snippet.md`. Add it if missing;
do not rewrite existing instructions.

### 7. Hand back

Summarize in a few lines, list every `[unknown: …]`, and ask the user to review.
Suggest `/backfill-spec` next if the repo already has shipped work.

## Constraints

- Facts from the repo or the user, never invented. Unknown stays `[unknown]`.
- No secrets, tokens, tenant or subscription IDs, internal hostnames or
  customer names in any spec. Describe the *kind* of value instead.
- Do not copy whole documents into the constitution. Link to them.
- Keep each file short enough to be always in context.
