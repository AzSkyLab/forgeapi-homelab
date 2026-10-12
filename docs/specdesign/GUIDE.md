# Spec-Driven Development with Claude: Team Guide

Adapted from the DeepLearning.AI course *Spec-Driven Development with Coding
Agents* and proven on a Rust and Godot game before being rewritten for API work.
Keep this open until the loop feels natural.

## The idea in one paragraph

You decide *what* gets built and which rules it must obey. Claude writes the
code. The bridge is a set of plain-English **spec files**, written before any
code, reviewed by you, and kept in sync afterwards. Specs survive between
sessions; Claude's memory of a conversation does not. Anything worth keeping
goes in a spec.

## The files that matter

| File | What it is | How often it changes |
|---|---|---|
| `specs/mission.md` | What the product is for, its principles, what it is *not* | Rarely |
| `specs/tech-stack.md` | Stack, architecture, conventions, **non-negotiables**, commands | Rarely |
| `specs/roadmap.md` | Small phases in order, ticked as they ship | Every phase |
| `specs/YYYY-MM-DD-<name>/requirements.md` | One phase: scope, decisions, failure modes | Per phase |
| `specs/YYYY-MM-DD-<name>/plan.md` | That phase's numbered task groups | Per phase |
| `specs/YYYY-MM-DD-<name>/validation.md` | How we know the phase is done | Per phase |
| `CHANGELOG.md` | What changed, by date | Every merge |

The first three are the **constitution**. Every phase is checked against it.
`CLAUDE.md` imports mission and tech-stack, so they are always in context.

## What is in the kit

| Piece | Where | Does |
|---|---|---|
| `/constitution` | `.claude/skills/constitution` | Drafts mission, tech-stack from the repo; interviews you for the rest |
| `/backfill-spec` | `.claude/skills/backfill-spec` | Writes as-built specs for work already shipped (the backlog) |
| `/feature-spec` | `.claude/skills/feature-spec` | Branch, interview, requirements + plan + validation for new work |
| `/finish-phase` | `.claude/skills/finish-phase` | Checks, reviewer, spec sync, roadmap tick, changelog |
| `/changelog` | `.claude/skills/changelog` | Builds `CHANGELOG.md` from git history |
| `reviewer` agent | `.claude/agents/reviewer.md` | Opus review of the branch against its spec; read-only |
| `test-runner` agent | `.claude/agents/test-runner.md` | Haiku runs tests and lints; returns a short summary |
| Templates | `specs/templates/` | Shape of every spec file |

## Setup (once per repo)

```bash
docs/specdesign/install.sh /path/to/repo      # copies the kit; never overwrites
cd /path/to/repo && claude
```

Then in order: `/constitution` → review → `/backfill-spec` → review → commit →
`/clear` → `/feature-spec`. [BACKLOG.md](BACKLOG.md) walks through the middle two.

## The loop (every phase)

| Step | You say | Claude does | You do |
|---|---|---|---|
| 1. Start | `/feature-spec` (optionally with a ticket or idea) | Finds or adds the phase, makes a branch | none |
| 2. Interview | Answer 3 questions | Asks about Scope, Decisions, Context | Be decisive; "you pick" is a fine answer |
| 3. Spec | none | Writes requirements, plan, validation | **Read them** (checklist below) |
| 4. Fix the spec | "Change the spec: …" | Edits the spec files | Repeat until right |
| 5. Build | "Implement task group 1" (or "the plan") | Builds group by group; tests via `test-runner` | Wait, or ask how it is going |
| 6. Prove | "Run the validation" | Runs automated checks; lists manual items | Run the manual checklist against a live instance |
| 7. Adjust | "Update the spec and code: …" | Changes both together | Re-test what changed |
| 8. Finish | `/finish-phase` | Checks, reviewer, spec sync, roadmap, changelog | Decide which findings to fix |
| 9. Ship | "Commit, push and open a PR" | Does exactly that | Review the PR as usual |
| 10. Reset | `/clear` | Fresh conversation | Next phase starts at step 1 |

Small phases are the point. If one feels big, say "split this phase."

## Reviewing a spec (5 minutes, the best use of your time)

Fixing a wrong assumption in a spec is free; fixing it in code costs a phase.

- [ ] **Scope:** anything in there you did not ask for? Anything missing?
- [ ] **Contract:** are routes, fields, status codes and error reasons right? Is compatibility with existing callers kept?
- [ ] **Failure modes:** what happens on bad input, retry, restart mid-operation, partial failure?
- [ ] **Constitution check:** does every non-negotiable hold? Any "What We Are Not Building" violated?
- [ ] **Security:** who may call it, whose data can it touch, can any secret leak into logs, responses or specs?
- [ ] **Validation:** would the listed tests fail if the feature were broken? Is there a refusal case for every rule?
- [ ] **Out of scope:** is something deferred that you need now?
- [ ] **Placeholders:** are `[bracketed]` names and numbers yours to fill in?

## Which model when (saving tokens)

| Step | Model | Set up by |
|---|---|---|
| Everyday building, fixing, chatting | Sonnet | `/model` (your default) |
| `/constitution`, `/backfill-spec`, `/feature-spec` | Opus | `model: opus` in the skill |
| Review before merge | Opus | the `reviewer` subagent |
| Running tests and lints | Haiku | the `test-runner` subagent |

If your plan does not offer a model, delete or change the `model:` line in that
skill or agent; it then uses your session model.

Habits matter more than model choice:

- **`/clear` between phases.** Every message re-sends the whole conversation.
- **Start spec work in a fresh session.** Each model keeps its own cache.
- **`/effort low`** for chores, **`/effort high`** for hard bugs.
- **Check `/usage`** after a phase.
- **Backfill in batches** of 3-5 phases per session, then `/clear`. The roadmap on disk is the memory.

## Phrases that work

| You want to… | Say |
|---|---|
| Start the next phase | `/feature-spec` |
| Spec a ticket | `/feature-spec` then paste the ticket |
| Change the plan before building | "Change the spec: the endpoint should …" |
| Build part of it | "Implement task group 1 only" |
| Understand something | "Walk me through how a request becomes an operation" |
| Get an honest opinion | "What are the risks of this design? What would you change?" |
| Make a rule stick forever | "Add that to the non-negotiables in tech-stack" |
| Change direction | "Update the roadmap: …", then "update existing specs and code to match" |
| Find weak spots | "Which behaviours in specs/ have no test?" |
| Check for drift | "Compare specs/ to the code. What no longer matches?" |
| Undo a bad direction | "Throw away the changes on this branch since the last commit" |

## When things go wrong

- **Claude is heading the wrong way:** press `Esc`, say what you actually want.
  Interrupting early is normal.
- **It built something you did not mean:** the spec was ambiguous. Say "update
  the spec to say …, then fix the code."
- **Tests fail:** ask why. Never ship with failing checks.
- **You are not sure it works:** ask for proof: test output, a request and
  response, emulator evidence. "It should work" is not done.
- **The conversation is muddled or slow:** park the step, `/clear`. The specs
  hold what matters.
- **A skill is not found:** the kit is not installed in this repo, or Claude
  Code was started in a different folder. Run `install.sh`, restart `claude`.

## Habits that make you better at this

1. **Review specs, not code.** Steer at the spec level, then read the diff.
2. **Answer interviews decisively.** Vague answers become guesses written down as decisions.
3. **"Done" means you ran it.** Tests prove it runs; only you can say it solves the problem.
4. **Correct once, record forever.** If you said it twice, it belongs in a spec,
   the non-negotiables, or `CLAUDE.md`.
5. **One phase per conversation.** Fresh context, sharper work.
6. **Bring references.** An existing endpoint to imitate, a ticket, a diagram.
7. **Ask why.** It teaches you the code and catches drift.

## Working at a company: extra rules

- **Public kit, private specs.** This kit is generic and safe to publish. Your
  repo's `specs/` may describe internal systems: keep them in the private repo.
- **No secrets in specs.** No tokens, keys, tenant or subscription IDs,
  hostnames, account numbers or customer names. Describe the *kind* of value
  (`the pod's workload identity`). Skills are told to refuse; reviewers should check.
- **PRs, not direct merges.** `/finish-phase` stops before commit, push and PR;
  you ask for those. Link the phase's `requirements.md` in the PR description so
  reviewers read intent first.
- **Ticket ids.** If branches carry ticket ids, say so once in `CLAUDE.md`; the
  skills follow it.
- **Shared repo, shared specs.** Specs are reviewed like code. Do not let two
  phases edit the same task group; split or sequence them in the roadmap.
- **Existing review process wins.** Specs add to CODEOWNERS, change control and
  security review, they do not replace them.

## Words you will see

- **Constitution:** mission + tech-stack + roadmap; the rules phases are checked against.
- **Phase:** one small shippable slice with its own spec directory.
- **Task group:** one numbered chunk of a plan, built and checked on its own.
- **Validation:** the proof a phase works: tests, evidence, a manual checklist.
- **Backfill / as-built spec:** a spec written after the fact from code and tests.
- **Non-negotiable:** a rule in tech-stack.md that every phase must hold to.
- **Drift:** the spec and the code no longer agree.
