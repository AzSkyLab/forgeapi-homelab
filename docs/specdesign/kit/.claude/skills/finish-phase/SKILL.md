---
name: finish-phase
description: Closes out a roadmap phase safely. Runs the validation checks, the reviewer, syncs specs with code, ticks the roadmap and updates the changelog. Use when the user invokes /finish-phase or says "finish the phase", "wrap up", "ready to merge". Commits, pushes and opens PRs only when the user asks.
model: sonnet
---

# Finish Phase

## Workflow

1. **Find the phase.** The newest `specs/YYYY-MM-DD-*/` touched on this branch
   (`git diff --name-only main...HEAD`). Read its `validation.md`.
2. **Automated checks.** Run the commands from `specs/tech-stack.md` through the
   `test-runner` subagent. Every test named in `validation.md` must exist; list any
   that do not. Failing checks stop the process: report them and fix first.
3. **Manual check.** Print the manual checklist from `validation.md`. Ask the user
   which items they ran and which passed. Unchecked items stay open; say so.
4. **Review.** Run the `reviewer` subagent. Present its findings ranked; the user
   decides what to fix. Re-run checks after fixes.
5. **Sync specs and code.** If the code legitimately drifted from the spec,
   update `requirements.md`, `plan.md` and `validation.md` to match. If the
   code is wrong instead, fix the code. Update any `docs/` pages the phase
   changed (contract, settings, runbooks), as listed in the plan.
6. **Roadmap.** Tick the phase's items in `specs/roadmap.md` and add the checkmark to its heading.
7. **Changelog.** Commit first (only if the user asked), then run `/changelog`.
8. **Report.** One short block: checks passed, review verdict, open manual
   items, files changed, what is left for the user (commit, push, PR, merge).

## Constraints

- Do not commit, push, open a PR or merge unless the user asked in this conversation.
- Never tick a phase with failing checks, an unrun manual item the user has not
  waived, or a reviewer verdict of "needs fixes" that is unanswered.
- "It should work" is not done. Cite test output.
