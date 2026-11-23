#!/usr/bin/env python3
"""SessionEnd hook: if the repo has .context/ and this session ended without /handoff
(NOW.md not written since the session started) but something changed in git, append a
snapshot to .context/current/AUTO.md. Keeps the newest 5 snapshots. Never fails the session.
Codex allows SessionEnd hooks 3 seconds at most, so all git calls share a 2.5 s budget."""
import json, os, subprocess, sys, time
from datetime import datetime

KEEP = 5
DEADLINE = time.time() + 2.5
SEP = "\n<!-- snapshot -->\n"


def git(root, *args):
    try:
        left = DEADLINE - time.time()
        if left <= 0:
            return ""
        return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=left).stdout.strip()
    except Exception:
        return ""


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        d = {}
    # The hook's `cwd` is where the session ended up (it follows `cd`); CLAUDE_PROJECT_DIR is
    # where it was opened. Snapshot both repos when they differ.
    dirs = [p for p in (d.get("cwd"), os.environ.get("CLAUDE_PROJECT_DIR")) if p] or [os.getcwd()]
    seen = []
    for cwd in dirs:
        root = git(cwd, "rev-parse", "--show-toplevel") or cwd
        if root in seen:
            continue
        seen.append(root)
        try:
            snapshot(root, d)
        except Exception:
            pass


def snapshot(root, d):
    ctx = os.path.join(root, ".context")
    if not os.path.isfile(os.path.join(ctx, "INDEX.md")):
        return
    now_md = os.path.join(ctx, "current", "NOW.md")

    # Session start = transcript file's creation time; fall back to 8 hours ago.
    start = time.time() - 8 * 3600
    tp = d.get("transcript_path")
    if tp and os.path.exists(tp):
        st = os.stat(tp)
        start = getattr(st, "st_birthtime", st.st_mtime)
    if os.path.exists(now_md) and os.path.getmtime(now_md) >= start:
        return  # /handoff ran this session

    commits = git(root, "log", "--oneline", f"--since=@{int(start)}", "-15")
    dirty = git(root, "status", "--short", "--", ".", ":(exclude).context")
    if not commits and not dirty:
        return  # nothing changed; nothing worth saying

    dirty_lines = dirty.splitlines()
    mins = int((time.time() - start) / 60)
    snap = [f"## {datetime.now():%Y-%m-%d %H:%M} · session ended without /handoff",
            f"- Branch: `{git(root, 'rev-parse', '--abbrev-ref', 'HEAD')}` · ~{mins} min · reason: {d.get('reason', '?')}",
            f"- Session: `{d.get('session_id', '?')}` (resume it to recover the conversation)"]
    if commits:
        snap += ["- Commits this session:"] + [f"  - {c}" for c in commits.splitlines()]
    if dirty_lines:
        snap += [f"- Uncommitted ({len(dirty_lines)}):"] + [f"  - `{l}`" for l in dirty_lines[:15]]
        if len(dirty_lines) > 15:
            snap.append(f"  - …and {len(dirty_lines) - 15} more")

    os.makedirs(os.path.dirname(now_md), exist_ok=True)
    auto = os.path.join(ctx, "current", "AUTO.md")
    old = open(auto, encoding="utf-8").read().split(SEP)[1:] if os.path.exists(auto) else []
    keep = (["\n".join(snap)] + old)[:KEEP]
    head = "# AUTO snapshots (newest first)\n\n> Written by a hook when a session ends without /handoff. NOW.md may be stale. /handoff folds these into NOW and deletes this file.\n"
    open(auto, "w", encoding="utf-8").write(head + SEP + SEP.join(keep) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
