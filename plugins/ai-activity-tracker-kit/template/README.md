# .context/ — how this system works

A small, structured memory for this repo so a fresh Claude session can pick up
where the last one stopped **without re-reading the codebase**.
Close a session with `/handoff`, open the next one (context auto-loads), or run `/pickup`.

## Two levels

- **Project** (`<client>/.context/`, e.g. `DEVELOPMENT/SchoolDays/.context/`): how the repos connect,
  shared DBs and services, product-wide conventions, cross-repo features and the product backlog.
- **Repo** (`<repo>/.context/`): how this repo's code works.

Same folders at both levels. **Each fact lives once**, at the level that matches its scope (true across repos → project,
true for one repo → that repo), and the other level points to it. A session in a repo loads the project
INDEX first, then the repo's INDEX + NOW.

## Folders

| Path | What lives here | Loaded |
|---|---|---|
| `INDEX.md` | One-screen map: what each file covers, the areas, last update | **Always** (hook) |
| `current/NOW.md` | The active task: goal, state, next step, open questions, files in play | **Always** (hook) |
| `todo/BACKLOG.md` | Prioritised work: Now / Next / Later / Blocked | When choosing work |
| `system/overview.md` | How the code works: architecture, entry points, data flow, key tables | When orienting |
| `system/<area>.md` | Deep notes on one area (e.g. `auth.md`, `payments.md`) | Only when working in that area |
| `conventions/code.md` | Naming, patterns, "how we do X here" | Before writing code |
| `conventions/workflow.md` | Branching, commits, build, deploy, environments | Before commit/deploy |
| `conventions/gotchas.md` | Things that have bitten us, and the fix | Before writing code |
| `decisions/LOG.md` | Dated decisions and *why* (newest first) | grep on demand |
| `history/YYYY-MM.md` | Session log, append-only | Never, unless asked |

## Rules (for Claude and humans)

1. **Token budget.** `INDEX.md` + `NOW.md` together stay under ~120 lines. Every
   other file under ~150 lines; split a growing `system/` file into areas.
2. **Pointers, not copies.** Reference code as `path/to/file.cs:123` or a symbol
   name. Never paste code blocks longer than ~5 lines; the code is the source of truth.
3. **Facts that outlive the task only** go into `system/`, `conventions/`,
   `decisions/`. Task-specific state goes in `NOW.md` and gets cleared when done.
4. **Overwrite, don't append** (`NOW.md`, `BACKLOG.md`, `system/`, `conventions/`).
   Only `decisions/LOG.md` and `history/` are append-only.
5. **Stale is worse than missing.** If a note contradicts the code, fix or delete
   the note in the same session.
6. **No secrets.** No connection strings, keys, tokens or customer data.
7. **CLAUDE.md stays small**: stack + commands + a short "never do" list + a pointer here.
8. **Git:** `system/`, `conventions/`, `decisions/`, `todo/` are committed and shared.
   `current/` and `history/` are personal (see `.context/.gitignore`), so each developer and
   each worktree keeps its own NOW.

## Session lifecycle

- **Start:** a SessionStart hook injects `INDEX.md`, `NOW.md`, any `current/AUTO.md`
  snapshot newer than NOW, and commits made since the last handoff. Claude reads anything else only when the task needs it.
- **End:** `/handoff` updates NOW, BACKLOG, any touched `system/` area, gotchas,
  decisions, appends to history, and bumps INDEX dates. Then close the session.
- **Forgot `/handoff`?** A SessionEnd hook writes `current/AUTO.md` (branch, uncommitted
  files, commits, minutes) so the next session can see what changed since NOW was written.
