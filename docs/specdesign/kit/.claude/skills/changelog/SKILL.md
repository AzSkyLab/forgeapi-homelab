---
name: changelog
description: Maintains CHANGELOG.md in the project root from git commit history. Use when the user invokes /changelog, asks to "update the changelog", or before merging or releasing a phase. Creates the file if missing (all commits grouped by date); otherwise adds only commits that aren't listed yet.
---

# Changelog

From the DeepLearning.AI spec-driven development course.

## Workflow

1. Commit the work first; the changelog is built from commits.
2. From the project root run:

   ```bash
   python3 .claude/skills/changelog/scripts/changelog.py
   ```

   - **No CHANGELOG.md**: writes every commit, grouped by date.
   - **CHANGELOG.md exists**: adds commits newer than the newest date heading,
     plus any commits from that date that aren't listed yet.
   - Merge commits are skipped. Re-running with nothing new changes nothing.

3. Read the new bullets and reword any that would confuse a reader who has not
   seen the code, then commit `CHANGELOG.md` with the change.

## Format

```markdown
# Changelog

## 2026-10-11

- Add spec-driven workflow: constitution, feature-spec and changelog skills

## 2026-10-10

- Initial release
```

Date headings are `## YYYY-MM-DD`, newest first, one bullet per commit subject.
