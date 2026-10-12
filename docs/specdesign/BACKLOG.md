# Backlogging Existing Work into Specs

You already built things. Before new specs mean anything, the repo needs a
constitution and as-built specs for what shipped. This is a one-time effort per
repo: expect an afternoon for a mid-sized codebase.

## Rules of the road

- **As-built, not as-wished.** The spec says what the code does today. Every
  statement points at evidence: a file, a test, a commit. What cannot be proven
  goes under **Unknowns** for a human, never into the requirements.
- **No code changes.** A bug found while backfilling goes under **Known gaps**.
- **Branch it.** `git checkout -b specs-backfill`. Specs get reviewed like code.
- **No secrets.** No tokens, IDs, hostnames, customer names.
- **A README is a claim, not proof.** The code or a test is proof.

## Steps

### 0. Install the kit

```bash
docs/specdesign/install.sh /path/to/work-repo
cd /path/to/work-repo && git checkout -b specs-backfill && claude
```

Commit the installed `.claude/`, `specs/templates/` and `CLAUDE.md` change on
their own so the backfill diff stays readable.

### 1. Constitution (~20 minutes)

Run `/constitution`. Claude reads the README, `AGENTS.md`/`CLAUDE.md`, `docs/`,
`pyproject.toml`, CI and the tests layout, drafts `specs/tech-stack.md` from
what it finds, then asks three questions (Purpose, Principles, Success) before
writing `specs/mission.md`.

**You review:**
- Every command in the tech-stack **Commands** table works (`[unverified]` ones: run them).
- **Non-negotiables** lists the rules your team actually enforces: auth, audit,
  secrets, approval gates, cost limits, what never touches production.
- Every `[unknown: …]` is answered or consciously left.

Commit: `Add constitution`.

### 2. Backfill (~1 hour per 3-5 phases)

`/clear`, then run `/backfill-spec`. Claude:

1. Inventories commits, merged PRs, `CHANGELOG`/`progress` docs, routes,
   settings, workers and tests.
2. Shows a **candidate phase table** (name, dates, commits, code, tests).
3. Asks three questions: Scope (grouping, what to skip), Decisions (summary vs
   detailed; date by last merge), Context (deviations only you know).
4. Writes `specs/YYYY-MM-DD-<name>/{requirements,plan,validation}.md` per phase
   and `specs/roadmap.md` with the phases ticked `✅ (backfilled)`.
5. Verifies every cited path and test exists and runs the cited tests.
6. Reports **orphans** (code with no phase), **gaps** (requirements with no
   test) and **unknowns**.

**You review each phase** with the spec checklist in GUIDE.md, plus:
- Does **Scope** match what the product really does, not what the README says?
- Is every **Decision** backed by `Evidence:`? Delete the ones that are guesses.
- Do **Unknowns** hold real questions? Answer them (tell Claude, it updates the spec).

For a big repo, run it in batches: "Backfill phases 1-4 only." Commit after each
batch, `/clear`, and carry on. The roadmap shows where you stopped.

### 3. Put the open work on the roadmap

Anything in flight or planned (a TODO list, an issue board, a Parked list) goes
under the ticked phases as unticked phases. Tell Claude: "Add these to the
roadmap as phases, smallest shippable slices: …". Do not write their specs yet;
`/feature-spec` does that one at a time.

### 4. Drift check

Ask: "Compare `specs/` to the code. List behaviours the code has that no spec
mentions, and spec statements the code contradicts." Fix specs, or note the
code issue in **Known gaps**. Then: "Which requirements have no test?" Turn the
worthwhile ones into the first small phases ("Close test gaps in …").

### 5. Merge the specs, then start new work

Merge the `specs-backfill` PR. New work now follows the loop in GUIDE.md:
`/clear`, `/feature-spec`.

## Prompts you can paste

| Situation | Say |
|---|---|
| Start | `/constitution` then `/backfill-spec` |
| Narrow the batch | "Backfill only the auth, tenancy and audit areas first." |
| A phase is wrong | "Phase 3's scope is wrong: X is not implemented, it is a stub. Fix the spec and record it under Known gaps." |
| Answering unknowns | "Unknown #2: the retry limit is 3 because of provider rate limits. Update the decision." |
| Too detailed | "Rewrite phases 5-8 at summary depth, one page each." |
| Resume next day | `/clear` then "Continue `/backfill-spec` from the first unticked area in roadmap.md." |
| Cold check | "Pretend you've never seen this repo. Read only `specs/` and tell me what the product does and where you're unsure." |

## What good looks like

- A new engineer can read `specs/` in an hour and know what exists and why.
- Every phase directory has all three files and a verified-at commit.
- `roadmap.md` reads as the project's history, then its future.
- Unknowns are a short list with owners, not empty because they were guessed away.
