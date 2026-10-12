---
name: backfill-spec
description: Writes specs for work that has already shipped (the backlog) by reading code, tests, git history and docs. Use after /constitution on an existing repo, when the user invokes /backfill-spec, or says "backlog the current work", "write specs for what we already built", "document existing features as specs". Produces as-built specs under specs/YYYY-MM-DD-<name>/ and a ticked roadmap. Never changes code.
model: opus
effort: high
---

# Backfill Spec

Turns shipped work into the same spec shape new work uses, so the repo has one
history and the reviewer has something to check against. The specs describe
**what the code does today**, proven by evidence, not what anyone wishes it did.

## Preconditions

- `specs/mission.md` and `specs/tech-stack.md` exist. If not, run `/constitution` first.
- Clean working tree. Work on a branch: `git checkout -b specs-backfill`.

## Workflow

### 1. Inventory the evidence (read-only)

Gather, per area of the product:

- History: `git log --format='%ad %h %s' --date=short`, merge commits, and merged
  PR titles if `gh` works (`gh pr list --state merged --limit 100`).
- Written records: `CHANGELOG.md`, `docs/progress.md`, ADRs, design notes, README.
- Code: top-level modules, HTTP routes (grep the router decorators), settings
  and env vars, background workers / workflows, migrations or schemas.
- Tests: test files by area and what they assert.

For a large repo, fan out: one read-only `Explore` subagent per candidate area
returns its facts (files, routes, settings, tests, commits). You write the
specs, so wording and format stay consistent.

### 2. Propose phases

Group the work into **candidate phases**: each a feature-sized slice that was
built together (a milestone, a PR series, one module and its tests). Show a
table in the conversation:

| # | Name | Dates | Key commits / PRs | Code | Tests |
|---|---|---|---|---|---|

Prefer 5-15 phases. Foundations (project skeleton, CI, auth) are phases too.
Do not split below "a thing a user or operator could name".

### 3. Interview, one call

Use `AskUserQuestion` with exactly **3 questions in one call**:

| Header | Focus |
|---|---|
| **Scope** | Is the grouping right? Which areas to skip (dead code, throwaway prototypes, archived rewrites)? |
| **Decisions** | Depth: *summary* (1 page per phase) or *detailed* (full as-built tables). Date each phase by its last merge (recommended) or first commit |
| **Context** | Known deviations: features half-done, flags that are off, behaviour that is wrong but intended to stay, things only the user knows |

Recommended option first. Do not write spec files until answered.

### 4. Write each phase

Create `specs/YYYY-MM-DD-<name>/` (date = when it shipped), from the templates:

**`requirements.md`**
- Header line: `Status: Shipped (backfilled <today> from commit <sha>)`.
- **Scope**: what the code does today, as short testable statements.
- **Decisions**: choices visible in the code, each ending `Evidence: <file>, <commit>`.
  The *why* is recorded only when a commit, doc or the user says it. Otherwise
  write `Why: [unknown]`.
- **Context**: how it fits with neighbouring phases; links to code.
- **Constitution check**: one line per non-negotiable in `tech-stack.md`: holds, or
  deviates (and where).
- **Known gaps**: behaviour that is missing, stubbed or untested.
- **Unknowns**: questions only a human can answer. Never fill these by guessing.

**`plan.md`**: "As built". The task groups as they exist in the code (module →
responsibility → key files), short. Not a reconstructed history.

**`validation.md`**
- **Automated**: the existing tests that cover this phase as `path::test_name`,
  grouped by requirement. Requirements with no test go under **Gaps**.
- **Manual**: how an engineer can see it working in a running instance.
- **Definition of done**: `Verified at <sha>`.

### 5. Build the roadmap

Create or extend `specs/roadmap.md`: one section per phase in date order,
`## Phase N — Name ✅` with the phase's items ticked `- [x]` and a final
`(backfilled)` note. Work still in progress or planned (from `docs/` or the
user) goes below as unticked phases. Never renumber a phase once written.

### 6. Verify, don't trust yourself

- Every `path::test_name` and file path cited exists (grep / `ls` each one).
- Run the cited tests through the `test-runner` subagent; record failures under **Known gaps**.
- Re-read each requirement against the code once. Delete or move to **Unknowns**
  any statement you cannot point at.

### 7. Report

Summarize: phases written, then three lists for the user: **code with no phase**
(orphans), **requirements with no test** (gaps), **unknowns** (questions).
Leave the commit to the user.

## Constraints

- As-built only. No invented rationale, no aspirational requirements.
- No code edits. If you find a bug, record it under **Known gaps**.
- No secrets, tenant / subscription / account IDs, hostnames or customer names
  in specs. Name the kind of value.
- A README or doc saying something is **not** evidence it works; the code or a
  test is. Say which you relied on.
- Large backlog: do 3-5 phases per session, then `/clear` and continue. The
  roadmap on disk is the memory between sessions.
