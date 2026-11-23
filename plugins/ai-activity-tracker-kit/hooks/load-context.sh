#!/usr/bin/env bash
# SessionStart hook for the .context/ system. Two levels:
#   project: a folder holding several repos (e.g. DEVELOPMENT/SchoolDays/.context)
#   repo:    the git repo root (e.g. SchoolDays/trusted-genie-api/.context)
# Injects project INDEX (+ project NOW if a cross-repo task is active), then the repo's
# INDEX, NOW, AUTO snapshot and git drift. Silent no-op when neither level exists.
set -uo pipefail

input="$(cat 2>/dev/null || true)"
# Prefer the hook input's cwd: at start-up it equals CLAUDE_PROJECT_DIR, but after /clear,
# /compact or a resume it is the folder the session has since moved to (cwd follows `cd`).
dir="$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("cwd") or "")' 2>/dev/null)"
[ -z "$dir" ] && dir="${CLAUDE_PROJECT_DIR:-}"
[ -z "$dir" ] && dir="$PWD"
cd "$dir" 2>/dev/null || exit 0

# Codex sets PLUGIN_ROOT for plugin hooks; Claude Code only sets CLAUDE_PLUGIN_ROOT.
if [ -n "${PLUGIN_ROOT:-}" ]; then tool=codex; pfx='$'; else tool=claude; pfx=/; fi

MAX=150
take() { [ -f "$1" ] || return; head -n "$MAX" "$1"; [ "$(wc -l < "$1")" -gt "$MAX" ] && echo "…(truncated at $MAX lines; ${pfx}handoff should trim this file)"; }
has_task() { [ -f "$1" ] && ! grep -q "^None in progress" "$1"; }

repo="$(git rev-parse --show-toplevel 2>/dev/null || true)"
base="${repo:-$dir}"

# Find the project level: nearest parent of $base with .context/INDEX.md, below $HOME.
find_parent_ctx() {
  local d; d="$(dirname "$1")"
  while [ -n "$d" ] && [ "$d" != "/" ] && [ "$d" != "$HOME" ]; do
    [ -f "$d/.context/INDEX.md" ] && { echo "$d"; return; }
    d="$(dirname "$d")"
  done
}
project="$(find_parent_ctx "$base")"
# Worktrees outside the project folder: resolve via the main worktree's location.
if [ -z "$project" ] && [ -n "$repo" ]; then
  common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  [ -n "$common" ] && project="$(find_parent_ctx "$(dirname "$common")")"
fi

# Primary level = repo (or the folder itself when it is the project folder).
primary=""
[ -f "$base/.context/INDEX.md" ] && primary="$base/.context"
[ -z "$primary" ] && [ -z "$project" ] && exit 0

out="This workspace uses the .context/ session-memory system (rules: .context/README.md).
Read other .context files only when the task needs them. Each fact lives at one level: true across
repos = project, true for this repo only = repo.
When a task is finished or the user is wrapping up, suggest ${pfx}handoff."

if [ -n "$project" ]; then
  out="$out

===== PROJECT: $project/.context/INDEX.md =====
$(take "$project/.context/INDEX.md")"
  if has_task "$project/.context/current/NOW.md"; then
    out="$out

===== PROJECT: current/NOW.md (cross-repo task) =====
$(take "$project/.context/current/NOW.md")"
  fi
fi

if [ -n "$primary" ]; then
  label="REPO"; [ -z "$repo" ] && label="PROJECT (session opened at project level)"
  # Skip INDEX when Claude Code already loads it through the CLAUDE.md import. Codex reads
  # AGENTS.md, which has no imports, so it always gets INDEX from here.
  if [ "$tool" = claude ] && grep -qs '^@\.context/INDEX\.md' "$base/CLAUDE.md"; then
    idx="(INDEX.md already loaded via CLAUDE.md import)"
  else
    idx="$(take "$primary/INDEX.md")"
  fi
  out="$out

===== $label: $primary/INDEX.md =====
$idx

===== $label: current/NOW.md =====
$(take "$primary/current/NOW.md")"

  auto="$primary/current/AUTO.md"
  if [ -f "$auto" ] && { [ ! -f "$primary/current/NOW.md" ] || [ "$auto" -nt "$primary/current/NOW.md" ]; }; then
    out="$out

===== $label: current/AUTO.md (sessions ended without ${pfx}handoff: NOW may be stale; mention this to the user) =====
$(head -n 60 "$auto")"
  fi

  if [ -n "$repo" ] && [ -f "$primary/current/NOW.md" ]; then
    since="$(stat -f '%Sm' -t '%Y-%m-%d %H:%M' "$primary/current/NOW.md")"
    commits="$(git -C "$repo" log --oneline --since="$since" -15 2>/dev/null)"
    dirty="$(git -C "$repo" status --short 2>/dev/null | wc -l | tr -d ' ')"
    out="$out

===== git drift since last handoff =====
Branch now: $(git -C "$repo" rev-parse --abbrev-ref HEAD) · uncommitted files: $dirty · NOW.md last written: $since"
    [ -n "$commits" ] && out="$out
Commits since then:
$commits"
  fi
elif [ -n "$repo" ] && main_wt="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)")" \
     && [ "$main_wt" != "$repo" ] && [ -f "$main_wt/.context/INDEX.md" ]; then
  out="$out

===== REPO (via main worktree, this branch doesn't carry .context/ yet): $main_wt/.context/INDEX.md =====
$(take "$main_wt/.context/INDEX.md")

(This is a worktree of $(basename "$main_wt") on branch $(git rev-parse --abbrev-ref HEAD). Read area files from
$main_wt/.context/. Do NOT run ${pfx}ctx-init here: .context/ arrives when main is merged into this branch.
${pfx}handoff in this worktree: write .context/current/NOW.md here (don't commit it; it's gitignored once
main's .context/ is merged in). Put durable notes in the main worktree's .context/ files instead.)"
elif [ -n "$repo" ]; then
  out="$out

(This repo, $(basename "$repo"), has no .context/ of its own yet. Offer ${pfx}ctx-init if the user works here a lot.)"
fi

python3 -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":sys.stdin.read()}}))' <<<"$out"
exit 0
