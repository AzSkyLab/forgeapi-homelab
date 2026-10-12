---
name: feature-spec
description: Starts the next roadmap phase spec-first. Finds the next incomplete phase in specs/roadmap.md (or creates one from the user's request or ticket), creates a git branch, interviews the user about scope, decisions and context, then writes specs/YYYY-MM-DD-<name>/ with requirements.md, plan.md and validation.md. Use when the user says "feature spec", "next phase", "start the next feature", "spec this ticket", or invokes /feature-spec.
model: opus
effort: high
---

# Feature Spec

Adapted from the DeepLearning.AI "Spec-Driven Development with Coding Agents"
course. No code is written in this skill; it ends with spec files for review.

## Workflow

### 1. Find the phase

Read `specs/roadmap.md`. The next phase is the first section whose items are all
`- [ ]`. If the user named a different phase, pasted a ticket, or described new
work that is not on the roadmap, use that and add it to the roadmap as the next
number (never renumber existing phases). Note its number and name.

### 2. Create the branch

```
git checkout main && git pull --ff-only && git checkout -b phase-N-<kebab-name>
```

If the repo uses ticket ids in branch names, follow that (see `CLAUDE.md`).
If the working tree has uncommitted changes, stop and ask what to do with them.

### 3. Read guidance and code

Read `specs/mission.md`, `specs/tech-stack.md` and the most recent spec
directory under `specs/` (for conventions and anything it deferred). Read the
code the phase will touch, and any ticket, doc or link the user gave.

### 4. Interview the user, BEFORE writing any files

Use `AskUserQuestion` with exactly **3 questions in one call**:

| Header | Question focus |
|---|---|
| **Scope** | What the phase includes and leaves out: who calls it, which endpoints or operations, which data, what the caller sees |
| **Decisions** | Key design choices: request / response shape, state and storage, error and failure behaviour, auth, limits, defaults |
| **Context** | Constraints and open questions: who depends on it, compatibility, cost, rollout, references to follow |

Give concrete options with a recommended default first, drawn from the code and
constitution (for example "follow the existing `/deployments` pattern"). Keep
each option one line of trade-off. If an answer would break a non-negotiable in
`tech-stack.md`, say so in the option.

Do **not** write any files until all three are answered.

### 5. Create the spec directory

Name: `specs/YYYY-MM-DD-<feature-name>/` using today's date.

#### `requirements.md`
- **Scope**: what is and is not included; tables for endpoints, fields, states
- **Decisions**: choices made and why (from the user's answers)
- **Context**: existing patterns to follow, links to code
- **Constitution check**: one line per non-negotiable in `tech-stack.md`: how this phase holds to it
- **Failure modes**: what happens on bad input, partial failure, retry, restart, timeout
- **Out of Scope**: what is deferred, and to which phase if known

#### `plan.md`
- Numbered task groups in implementation order (for example: Data / schema →
  Domain rules → API contract → Wiring (workers, config) → Tests → Docs). Each
  group has numbered sub-tasks and ends with a **Verify** step. Groups are
  independently reviewable.
- Contract changes are listed explicitly (new / changed / removed fields and
  routes), with the compatibility rule from `tech-stack.md`.
- New settings are listed with default and whether they are required.

#### `validation.md`
- **Automated**: the commands from `tech-stack.md` pass; list the **specific new
  tests and assertions** required, by file and behaviour (unit, API, workflow,
  integration). Include the refusal and failure-mode cases, not only the happy path.
- **Evidence**: any real-environment proof required (emulator run, staging call),
  and what to capture.
- **Manual check**: a checklist an engineer runs against a live instance, including edge cases.
- **Definition of done**: all of the above, specs in sync with the code, docs
  updated, roadmap phase ticked.

### 6. Hand back for review

Summarize the spec in a few lines and ask the user to review it before
implementation starts. Implementation begins only when they say so.

## Constraints

- Respect `specs/tech-stack.md` (including "What We Are Not Using": no new
  dependencies without approval) and `specs/mission.md` ("What We Are Not Building").
- Do not invent names, policies or numbers the user owns: use `[placeholder]`
  and ask.
- No secrets, IDs or customer names in specs.
- Keep the phase small and independently shippable. If the plan grows past about
  six task groups, propose splitting the phase in the roadmap.
