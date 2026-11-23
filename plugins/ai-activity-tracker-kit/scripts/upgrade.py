#!/usr/bin/env python3
"""Bring every .context/ folder under a root up to the current kit version.

  upgrade.py <root> [--apply]     dry run unless --apply

What it changes (kit-owned only, never your notes):
  - .context/README.md and .context/.gitignore  -> replaced with the kit's current copy
  - template files that don't exist yet          -> added (never overwrites an existing file)
  - .context/.kit-version                        -> set to the kit's version
What it only reports:
  - repo-level CLAUDE.md without the `@.context/INDEX.md` import
  - repo-level AGENTS.md (Codex) that doesn't mention .context/
  - files over the line budget (INDEX+NOW > 120, any other > 150)
"""
import json, os, shutil, subprocess, sys

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(KIT, "template")
VERSION = json.load(open(os.path.join(KIT, ".claude-plugin", "plugin.json")))["version"]
OWNED = ["README.md", ".gitignore"]
SKIP = {"node_modules", ".git", "obj", "bin", "dist", ".angular", "archive"}


def find_contexts(root):
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP and not x.startswith(".") or x == ".context"]
        if os.path.basename(d) == ".context" and "INDEX.md" in files:
            dirs[:] = []
            yield d
        if d.count(os.sep) - root.count(os.sep) > 5:
            dirs[:] = []


def is_repo_root(folder):
    return os.path.exists(os.path.join(folder, ".git"))


def lines(p):
    try:
        return sum(1 for _ in open(p, encoding="utf-8"))
    except Exception:
        return 0


def plan_for(ctx):
    acts, notes = [], []
    for name in OWNED:
        src, dst = os.path.join(TEMPLATE, name), os.path.join(ctx, name)
        if not os.path.exists(dst) or open(src, "rb").read() != open(dst, "rb").read():
            acts.append(("replace" if os.path.exists(dst) else "add", src, dst))
    for d, _dirs, files in os.walk(TEMPLATE):
        for f in files:
            src = os.path.join(d, f)
            rel = os.path.relpath(src, TEMPLATE)
            if rel in OWNED or rel == "INDEX.md":
                continue
            dst = os.path.join(ctx, rel)
            if not os.path.exists(dst):
                acts.append(("add", src, dst))
    vf = os.path.join(ctx, ".kit-version")
    old = open(vf).read().strip() if os.path.exists(vf) else "none"
    if old != VERSION:
        acts.append(("version", old, VERSION))

    folder = os.path.dirname(ctx)
    if is_repo_root(folder):
        cm = os.path.join(folder, "CLAUDE.md")
        if not os.path.exists(cm) or "@.context/INDEX.md" not in open(cm, encoding="utf-8").read():
            notes.append("CLAUDE.md has no `@.context/INDEX.md` import (teammates without the kit won't get INDEX)")
        am = os.path.join(folder, "AGENTS.md")
        if os.path.exists(am) and ".context/" not in open(am, encoding="utf-8").read():
            notes.append("AGENTS.md doesn't point to .context/ (Codex users without the kit won't find it)")
    base = lines(os.path.join(ctx, "INDEX.md")) + lines(os.path.join(ctx, "current", "NOW.md"))
    if base > 120:
        notes.append(f"INDEX + NOW = {base} lines (budget 120)")
    for d, _dirs, files in os.walk(ctx):
        if "archive" in d or "history" in d:
            continue
        for f in files:
            p = os.path.join(d, f)
            if f.endswith(".md") and f not in ("INDEX.md", "NOW.md") and lines(p) > 150:
                notes.append(f"{os.path.relpath(p, ctx)} = {lines(p)} lines (budget 150)")
    return acts, notes


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--apply" in sys.argv
    root = os.path.abspath(os.path.expanduser(args[0] if args else "."))
    print(f"ai-activity-tracker-kit {VERSION} · {'APPLYING' if apply else 'dry run'} · root {root}\n")
    found = 0
    for ctx in find_contexts(root):
        found += 1
        folder = os.path.dirname(ctx)
        git = os.path.join(folder, ".git")
        level = "worktree" if os.path.isfile(git) else ("repo" if os.path.isdir(git) else "project")
        acts, notes = plan_for(ctx)
        print(f"{os.path.relpath(folder, root) or '.'}  [{level}]")
        if not acts and not notes:
            print("  up to date")
        for kind, a, b in acts:
            if kind == "version":
                print(f"  version {a} -> {b}")
                if apply:
                    open(os.path.join(ctx, ".kit-version"), "w").write(b + "\n")
            else:
                print(f"  {kind} {os.path.relpath(b, folder)}")
                if apply:
                    os.makedirs(os.path.dirname(b), exist_ok=True)
                    shutil.copyfile(a, b)
        for n in notes:
            print(f"  note: {n}")
        if apply and acts and level != "project":
            st = subprocess.run(["git", "-C", folder, "status", "--short", "--", ".context"],
                                capture_output=True, text=True).stdout.strip()
            if st:
                print("  git: .context changes to commit in this repo")
    if not found:
        print("No .context/ folders found.")


if __name__ == "__main__":
    main()
