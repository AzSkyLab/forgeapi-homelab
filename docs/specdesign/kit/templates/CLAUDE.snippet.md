## How we work: spec-driven

Specs come before code. The guide is `docs/specdesign/GUIDE.md`
(or the copy your team keeps).

- **Constitution:** `specs/mission.md` (what the product is and is not),
  `specs/tech-stack.md` (stack, architecture, non-negotiables, commands),
  `specs/roadmap.md` (small phases). Mission and tech-stack are imported below,
  so they are always in context.
- **New work starts with `/feature-spec`**: branch, interview the user with
  AskUserQuestion (Scope / Decisions / Context), then write
  `specs/YYYY-MM-DD-<name>/` (requirements, plan, validation). No code until the
  user approves the spec.
- **Existing work is documented with `/backfill-spec`** (as-built specs with
  evidence). **First-time setup** is `/constitution`.
- **Keep specs in sync.** When direction changes mid-phase, update the phase spec
  (and the constitution if it is a lasting rule) along with the code.
- **Finishing a phase:** `/finish-phase` (checks, reviewer, specs synced, roadmap
  ticked, `/changelog`). Commit, push and open PRs only when the user asks.
- **Models (token budget):** the main session runs Sonnet; `/feature-spec`,
  `/constitution` and `/backfill-spec` run on Opus. Run test suites through the
  `test-runner` subagent (Haiku) so logs stay out of context, and the `reviewer`
  subagent (Opus) before merging a phase.
- **Names and policy:** do not invent names, policies, limits or numbers the user
  owns; use bracketed placeholders and ask.
- **No secrets in specs.** No tokens, tenant or subscription IDs, hostnames or
  customer names. Describe the kind of value.

@specs/mission.md
@specs/tech-stack.md
