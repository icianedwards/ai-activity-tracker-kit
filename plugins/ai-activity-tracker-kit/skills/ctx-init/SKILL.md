---
name: ctx-init
description: Set up the .context/ session-memory folder (system, current, todo, conventions, decisions, history) in the current repo and seed it from the code. Use for /ctx-init, "set up context for this repo", "add the context system here".
---

# /ctx-init: scaffold and seed `.context/`

> **Paths:** `<kit>` below means this plugin's root folder: two levels up from this skill's base directory (the folder holding this SKILL.md) (`<kit>/scripts`, `<kit>/template`).


Goal: a repo where the next session can start from `.context/INDEX.md` + `current/NOW.md`
instead of re-exploring. Rules for the folder are in the template's `README.md`. Follow them.

## Steps

1. **Pick the level.**
   - **Repo**: a git repo root (`git rev-parse --show-toplevel`). Scaffold with
     `bash <kit>/scripts/scaffold.sh <root>`.
   - **Project**: a folder holding several repos of one product/client (e.g. `DEVELOPMENT/SchoolDays`).
     Scaffold with `bash <kit>/scripts/scaffold.sh --project <folder>`.
   If the user is in a repo whose parent folder holds sibling repos and has no `.context/`, offer the
   project level too. If `.context/` already exists, stop and offer `/handoff` or a targeted refresh.
2. **What goes where.** Each fact lives once, at the level that matches its scope (across repos →
   project, one repo → that repo). Project: how the repos connect, shared DBs and external services, product-wide
   conventions and gotchas, cross-repo features, the product backlog. Repo: how *this* code works.
   Never copy a fact into both; point to the other level instead.
3. **Seed it from what's cheap and reliable**, not from a full read of the repo:
   - Existing `CLAUDE.md`, `README*`, solution/project files, `package.json` scripts,
     top two levels of the folder tree, `git log --oneline -20`, and any `docs/` or db `scripts/` folders.
   - **Project level:** list every repo in the INDEX Repos table (role, whether it has `.context/`;
     skip worktree copies, name them under their main repo). Seed from loose notes in the folder
     (`*.md`, `handoffs/`): move them in verbatim, archive the original under `.context/archive/`,
     and leave a pointer file where it was.
   - Fill `INDEX.md` (one-sentence purpose, stack), `system/overview.md`,
     `conventions/workflow.md` (build/run/test/deploy), and `conventions/code.md` from 1–2 representative files.
   - If the repo is large, add 2–5 `system/<area>.md` stubs (one line each: purpose + entry file)
     and list them in the INDEX areas table. Don't fill them; they fill up as work touches them.
   - Move any conventions/gotchas already in `CLAUDE.md` into `conventions/`; leave `CLAUDE.md`
     with stack, commands, a short **Never do** list (only rules whose breach is costly and that
     a session might hit without reading the area file), and this pointer section:
     ```
     ## Context system
     Session memory lives in `.context/` (rules: `.context/README.md`). INDEX + current/NOW
     load at session start; read other files only when the task needs them. End a task with `/handoff`.

     @.context/INDEX.md
     ```
     The `@` import gives teammates without the hooks the INDEX too; the hook then skips it.
   - **Codex reads `AGENTS.md`, not `CLAUDE.md`.** If the repo has an `AGENTS.md`, or the user
     works in Codex, give it the same pointer section with `$handoff` instead of `/handoff` and,
     in place of the `@` line (Codex has no imports), the line
     `Read .context/INDEX.md and .context/current/NOW.md before starting a task.`
     Keep stack/commands in one of the two files and point to it from the other; don't duplicate them.
   - If this session already has task context, write it into `current/NOW.md` and `todo/BACKLOG.md`.
4. **Mark unknowns as `TODO`**. Never invent facts. No secrets.
5. **Git**: don't commit. The template's `.context/.gitignore` keeps `current/` and `history/`
   personal; everything else is shared. Tell the user the folder is ready to commit.
6. Report: files created, what was seeded vs left TODO, line count of INDEX + NOW.
