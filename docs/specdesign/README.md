# Spec-Driven Development Kit

A drop-in kit of Claude Code skills, agents and templates for spec-first work on
an existing codebase: document what you shipped, then spec each new feature
before any code is written. Based on the DeepLearning.AI course
*Spec-Driven Development with Coding Agents*.

Generic by design: nothing in here is specific to one company or product.
Safe to publish and pull into a work environment.

## 5-minute start

```bash
git clone <this-repo> && cd <this-repo>
docs/specdesign/install.sh /path/to/your/repo      # add --dry-run to preview
cd /path/to/your/repo && claude
```

Then, in Claude Code:

1. `/constitution` : draft `specs/mission.md` and `specs/tech-stack.md` from the repo
2. `/backfill-spec` : as-built specs for everything already shipped
3. `/clear`, then `/feature-spec` : spec the next piece of work
4. build, then `/finish-phase`

## Read these

| File | For |
|---|---|
| [GUIDE.md](GUIDE.md) | The workflow, the loop, spec review checklist, phrases, company rules |
| [BACKLOG.md](BACKLOG.md) | Step by step: turning existing work into specs |

## What is in the box

```
docs/specdesign/
├── README.md  GUIDE.md  BACKLOG.md
├── install.sh                     copies the kit into a repo, never overwrites
└── kit/
    ├── .claude/
    │   ├── skills/
    │   │   ├── constitution/      mission + tech-stack from evidence
    │   │   ├── backfill-spec/     as-built specs for shipped work
    │   │   ├── feature-spec/      interview, then requirements/plan/validation
    │   │   ├── finish-phase/      checks, review, spec sync, roadmap
    │   │   └── changelog/         CHANGELOG.md from git (+ script)
    │   └── agents/
    │       ├── reviewer.md        Opus, read-only branch review against the spec
    │       └── test-runner.md     Haiku, runs tests and lints, short report
    └── templates/                 mission, tech-stack, roadmap, requirements,
                                   plan, validation, CLAUDE.md snippet
```

Installed into a repo it lands as `.claude/`, `specs/templates/`,
`docs/specdesign/{GUIDE,BACKLOG}.md`, and a spec-driven section in `CLAUDE.md`.

## Requirements

- Claude Code with skills and subagents.
- `git`, `bash`, `python3` (changelog script). `gh` is optional (backfill reads merged PRs).
- Opus, Sonnet and Haiku are assumed. Delete a skill's or agent's `model:` line to use your session model.

## Updating the kit

Re-run `install.sh`. It skips files that differ from the kit so your edits are
safe; `--force` overwrites. Improve a skill in a work repo? Copy the change back
into `kit/` here so the next repo gets it.
