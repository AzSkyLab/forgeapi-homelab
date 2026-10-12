# Quick Start

Two ways in. Both end with the same result: a constitution, as-built specs for
what you already shipped, and a spec-first loop for new work.

## A. By hand (5 minutes)

```bash
git clone <this-repo> /tmp/specdesign-kit
/tmp/specdesign-kit/docs/specdesign/install.sh /path/to/work-repo    # --dry-run to preview
cd /path/to/work-repo && git checkout -b specs-backfill && claude
```

Then: `/constitution` → review → `/backfill-spec` → review → commit → `/clear` →
`/feature-spec`. Details: [BACKLOG.md](BACKLOG.md), [GUIDE.md](GUIDE.md).

## B. Hand it to your LLM

Paste the prompt below into a fresh session opened in the **work repo**. Replace
the two `<…>` values. It works in Claude Code (slash commands) and in any other
coding agent that can read files and run shell commands (it falls back to
following the `SKILL.md` files as written instructions).

````text
You are helping me adopt spec-driven development in this repository. A kit for
it lives in a public repo: <KIT_REPO_URL> (path docs/specdesign/, branch <BRANCH>).

Work in this order and stop at each CHECKPOINT for my review. Do not commit,
push, or open a PR unless I ask. Do not change application code. Never put
secrets, tokens, tenant/subscription/account IDs, hostnames or customer names
in any file you write; describe the kind of value instead.

1. FETCH. Clone the kit to a temporary directory OUTSIDE this repo
   (e.g. /tmp/specdesign-kit), read its docs/specdesign/README.md, GUIDE.md and
   BACKLOG.md, and run `docs/specdesign/install.sh <this repo's root> --dry-run`.
   Show me what it would add. CHECKPOINT: wait for my OK, then run it for real
   (without --force) and create the branch `specs-backfill`.

2. CONSTITUTION. Run /constitution. If slash commands are unavailable, read
   .claude/skills/constitution/SKILL.md and follow it exactly. Draft
   specs/tech-stack.md from the repo's own evidence and interview me for
   specs/mission.md. Mark anything you cannot establish as [unknown: question].
   CHECKPOINT: list every command in the Commands table you could not verify
   and every [unknown].

3. BACKFILL. Run /backfill-spec (or follow .claude/skills/backfill-spec/SKILL.md).
   Propose the phase table first and ask me the three interview questions
   before writing. Document 3-5 phases per batch, as-built only: every claim
   cites a file, test or commit; anything unproven goes under Unknowns.
   CHECKPOINT after each batch: phases written, orphans (code with no phase),
   gaps (requirements with no test), unknowns.

4. ROADMAP AND DRIFT. Add planned and in-flight work to specs/roadmap.md as
   unticked phases (do not write their specs yet). Compare specs/ to the code
   and list drift. CHECKPOINT: summary for me to review.

5. HAND-OFF. Tell me the exact git commands to commit the work in separate
   commits (kit install, constitution, each backfill batch) and how to start the
   first new feature with /feature-spec after a /clear.

Rules throughout: facts from the repo or from me, never invented; ask when
unsure; keep specs short; follow specs/tech-stack.md for how to run tests.
````

Placeholders: `<KIT_REPO_URL>` is the public repo that holds this kit,
`<BRANCH>` the branch to read (`main` once merged).

## After setup

Each new piece of work: `/clear` → `/feature-spec` → review the three spec files →
"Implement task group 1" … → `/finish-phase` → ask for the commit and PR.
