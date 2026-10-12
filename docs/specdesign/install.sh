#!/usr/bin/env bash
# Install the spec-driven kit into a repository.
#
#   docs/specdesign/install.sh /path/to/target-repo [--force] [--dry-run]
#
# Copies skills, agents and templates; never overwrites an existing file
# unless --force is given. Safe to re-run.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
kit="$here/kit"
target=""
force=0
dry=0

for arg in "$@"; do
  case "$arg" in
    --force) force=1 ;;
    --dry-run) dry=1 ;;
    -h|--help) sed -n '2,8p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) target="$arg" ;;
  esac
done

[ -n "$target" ] || { echo "usage: install.sh /path/to/target-repo [--force] [--dry-run]" >&2; exit 2; }
[ -d "$target" ] || { echo "not a directory: $target" >&2; exit 2; }
target="$(cd "$target" && pwd)"
[ -d "$target/.git" ] || echo "warning: $target is not the root of a git repo" >&2

say() { if [ "$dry" = 1 ]; then echo "[dry-run] $*"; else echo "$*"; fi; }

copy_file() { # copy_file <src> <dest>
  local src="$1" dest="$2"
  if [ -e "$dest" ] && [ "$force" = 0 ]; then
    if cmp -s "$src" "$dest"; then return; fi
    say "skip (exists, differs): ${dest#"$target"/}   (use --force to overwrite)"
    return
  fi
  say "install: ${dest#"$target"/}"
  [ "$dry" = 1 ] && return
  mkdir -p "$(dirname "$dest")"
  cp "$src" "$dest"
}

copy_tree() { # copy_tree <src_dir> <dest_dir>
  local src="$1" dest="$2" f rel
  while IFS= read -r -d '' f; do
    rel="${f#"$src"/}"
    copy_file "$f" "$dest/$rel"
  done < <(find "$src" -type f ! -name '*.pyc' ! -path '*/__pycache__/*' -print0)
}

copy_tree "$kit/.claude" "$target/.claude"
copy_tree "$kit/templates" "$target/specs/templates"
copy_file "$here/QUICKSTART.md" "$target/docs/specdesign/QUICKSTART.md"
copy_file "$here/GUIDE.md" "$target/docs/specdesign/GUIDE.md"
copy_file "$here/BACKLOG.md" "$target/docs/specdesign/BACKLOG.md"
[ "$dry" = 1 ] || chmod +x "$target/.claude/skills/changelog/scripts/changelog.py" 2>/dev/null || true

# Project instructions: Claude Code reads CLAUDE.md.
snippet="$kit/templates/CLAUDE.snippet.md"
marker="How we work: spec-driven"
claude="$target/CLAUDE.md"
if [ -f "$claude" ] && grep -q "$marker" "$claude"; then
  :
elif [ -f "$claude" ]; then
  say "append spec-driven section to CLAUDE.md"
  if [ "$dry" = 0 ]; then { printf '\n'; cat "$snippet"; } >> "$claude"; fi
else
  say "create CLAUDE.md"
  if [ "$dry" = 0 ]; then
    {
      echo "# Notes for working on this repo"
      echo
      if [ -f "$target/AGENTS.md" ]; then echo "@AGENTS.md"; echo; fi
      cat "$snippet"
    } > "$claude"
  fi
fi

suffix=""; [ "$dry" = 1 ] && suffix=" (dry run)"
cat <<EOF

Done$suffix. Next, in $target:
  1. claude                      # start Claude Code in the repo
  2. /constitution               # draft specs/mission.md and specs/tech-stack.md
  3. /backfill-spec              # write as-built specs for work already shipped
  4. /clear, then /feature-spec  # first new phase
Guide: docs/specdesign/GUIDE.md   Backlog how-to: docs/specdesign/BACKLOG.md
EOF
