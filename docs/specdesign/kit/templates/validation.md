# Phase N — [Name]: Validation

## Automated

- The test, lint and format commands from `specs/tech-stack.md` pass.

New tests that must exist and pass:

**[Area: e.g. domain rules] (`tests/test_[x].py`)**
- [Behaviour asserted, with the concrete input and expected result.]
- Refused: [each bad input, with the expected status and reason].

**[Area: e.g. API] (`tests/test_[y].py`)**
- [...]

(Backfilled specs: list existing tests as `path::test_name` under each
requirement, and put requirements with no test under **Gaps**.)

## Gaps *(backfilled specs)*

- [Requirement with no test.]

## Evidence

[Real-environment proof required, if any: emulator run, staging call. What to capture.]

## Manual check

A checklist an engineer runs against a live instance.

- [ ] [Do X; expect Y.]
- [ ] [Edge case: do X with bad input; expect refusal Z.]

## Definition of done

All of the above; specs in sync with the code; docs updated; roadmap phase
ticked. (Backfilled: `Verified at <sha>`.)
