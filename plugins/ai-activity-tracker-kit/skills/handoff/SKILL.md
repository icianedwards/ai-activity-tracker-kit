---
name: handoff
description: End-of-session save. Updates the repo's .context/ files (NOW, BACKLOG, system areas, conventions, gotchas, decisions, history, INDEX) so the next session can start cold with minimal tokens. Use for /handoff, "wrap up", "save context", "I'm closing this session".
---

# /handoff: write the session into `.context/`

> **Paths:** `<kit>` below means this plugin's root folder: two levels up from this skill's base directory (the folder holding this SKILL.md) (`<kit>/scripts`, `<kit>/template`).


Run in the repo root (`git rev-parse --show-toplevel`). If there is no `.context/`, offer `/ctx-init` and stop.
**Two levels:** the repo's `.context/` and, if one exists, the project level in a parent folder
(e.g. `DEVELOPMENT/SchoolDays/.context/`). Put each fact at the level that matches its scope: cross-repo flows, shared DB/services, product-wide gotchas and product backlog go to
the project; anything about this repo's code goes to the repo. Never write the same fact in both.
If the session touched several repos, update each repo that has a `.context/`; a cross-repo task also
gets the project `current/NOW.md` (the repos' NOWs point to it instead of repeating it).
Read `.context/README.md` once if its rules aren't already in context.

## 1. Gather (cheap)
- What this conversation did, decided and learned. That's the main source.
- `current/AUTO.md`, if present: snapshots from sessions that ended without a handoff.
  Fold anything still relevant into NOW, then delete AUTO.md.
- `git status --short`, `git rev-parse --abbrev-ref HEAD`, `git log --oneline -10`,
  and ahead/behind vs origin. Don't re-read the code to write notes.

## 2. Update files: overwrite where the rules say overwrite

| File | Update |
|---|---|
| `current/NOW.md` | Create it if missing (it isn't committed). Rewrite fully. Task, State (done / half-done / **verified or not**), the single **Next step**, open questions with owner, files in play as `path:line`. Uncommitted or unpushed work goes in State. If the task is finished, reset to "None in progress" |
| `todo/BACKLOG.md` | Add follow-ups found, remove finished items, re-prioritise Now/Next/Later/Blocked |
| `system/<area>.md` | Only areas this session **learned something durable** about: how it works, entry points, non-obvious flows. Create the file and add it to INDEX if it's a new area |
| `conventions/*.md` | New pattern, deploy step or command learned. Gotchas: `symptom → cause → rule (date)` |
| `decisions/LOG.md` | Prepend an entry for each real decision made (esp. ones the user chose), with why and rejected options |
| `history/YYYY-MM.md` | Append: `### YYYY-MM-DD: <task>` + 3–6 bullets (what changed, commits/branch, unfinished) |
| `INDEX.md` | Bump "Last handoff" date and branch; keep the areas table in sync |

## 3. Enforce the budget
- INDEX + NOW together under ~120 lines; any other file under ~150. Split or compress if over.
- Delete notes the session proved wrong. Pointers, not pasted code. No secrets.

## 4. Report back, briefly
- A short list of files changed, with one line each on what changed.
- The **Next step** from NOW.md, verbatim.
- Any loose ends: uncommitted/unpushed work, unverified changes, items waiting on someone.
- Durable repo facts that belong in `CLAUDE.md` or `AGENTS.md` (stack, commands): offer to add them there too.
- **Time:** run `python3 <kit>/scripts/ts.py summary` and show today's tracked hours
  per client in one line. Add `- Time: ~Xh tracked` to the history entry. Offer `/timesheet`
  to log it; never log without the user's OK.

Don't commit unless the user asks. Then tell them it's safe to close the session.
