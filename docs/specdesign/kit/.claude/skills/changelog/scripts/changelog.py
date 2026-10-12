#!/usr/bin/env python3
"""Maintain CHANGELOG.md from git commit history.

Based on the DeepLearning.AI course script, changed to skip merge commits and
to pick up later commits made on the same day as the newest entry.
"""

import re
import subprocess
from collections import defaultdict
from pathlib import Path

DATE_HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s*$")


def git_log(since_date=None):
    """Return {date: [subject, ...]} with subjects oldest-first within a day."""
    cmd = ["git", "log", "--no-merges", "--reverse", "--format=%ad|%s", "--date=short"]
    if since_date:
        cmd.append(f"--since={since_date} 00:00")
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    by_date = defaultdict(list)
    for line in out.splitlines():
        date, _, subject = line.partition("|")
        if subject:
            by_date[date.strip()].append(subject.strip())
    return by_date


def parse(text):
    """Return (sections, order): {date: [bullet, ...]} and dates in file order."""
    sections, order, current = {}, [], None
    for line in text.splitlines():
        m = DATE_HEADING.match(line)
        if m:
            current = m.group(1)
            sections[current] = []
            order.append(current)
        elif current and line.startswith("- "):
            sections[current].append(line[2:].strip())
    return sections, order


def render_section(date, subjects):
    return f"## {date}\n\n" + "".join(f"- {s}\n" for s in subjects)


def main():
    path = Path("CHANGELOG.md")

    if not path.exists():
        by_date = git_log()
        if not by_date:
            print("No commits found; nothing to write.")
            return
        body = "\n".join(render_section(d, by_date[d]) for d in sorted(by_date, reverse=True))
        path.write_text("# Changelog\n\n" + body)
        print(f"Created CHANGELOG.md with {sum(map(len, by_date.values()))} entries.")
        return

    text = path.read_text()
    sections, order = parse(text)
    newest = order[0] if order else None
    by_date = git_log(since_date=newest)

    added = 0
    new_dates = sorted((d for d in by_date if newest is None or d > newest), reverse=True)
    if newest in by_date:
        missing = [s for s in by_date[newest] if s not in sections[newest]]
        if missing:
            # Append to the newest section, right after its last bullet.
            lines = text.splitlines(keepends=True)
            start = next(i for i, l in enumerate(lines) if DATE_HEADING.match(l) and newest in l)
            end = start + 1
            while end < len(lines) and not DATE_HEADING.match(lines[end]):
                end += 1
            last_bullet = max((i for i in range(start, end) if lines[i].startswith("- ")), default=start + 1)
            lines[last_bullet + 1:last_bullet + 1] = [f"- {s}\n" for s in missing]
            text = "".join(lines)
            added += len(missing)

    if new_dates:
        head, sep, rest = text.partition("\n## ")
        new = "".join(render_section(d, by_date[d]) + "\n" for d in new_dates)
        text = head.rstrip("\n") + "\n\n" + new + (("## " + rest) if sep else "")
        added += sum(len(by_date[d]) for d in new_dates)

    if added == 0:
        print("No new commits; CHANGELOG.md is up to date.")
        return
    path.write_text(text)
    print(f"Added {added} entries to CHANGELOG.md.")


if __name__ == "__main__":
    main()
