---
name: pickup
description: Start-of-session brief from the repo's .context/ folder. Reads INDEX + current/NOW, checks git for drift since the last handoff, and gives a 5-line brief. Use for /pickup, "where was I", "what's the state of this repo", "catch me up".
---

# /pickup: resume from `.context/`

1. In the repo root, read the parent project's `.context/INDEX.md` (if there is one; plus its
   `current/NOW.md` when it holds a cross-repo task), then the repo's `.context/INDEX.md` and `.context/current/NOW.md`
   (skip if the SessionStart hook already put them in context). Also `current/AUTO.md` if it
   exists: it means the last session(s) ended without `/handoff`, so NOW may be stale.
   No `.context/`? Say so and offer `/ctx-init`.
2. Check drift: `git rev-parse --abbrev-ref HEAD`, `git status --short`,
   `git log --oneline` since NOW's "Updated" date. Flag: branch differs from NOW,
   teammates' commits touching files in play, uncommitted work NOW doesn't mention.
3. Don't read `system/`, `conventions/`, `decisions/` or `history/` yet.
   Read them later only when the next step needs them.
4. Reply with at most ~6 lines:
   - **Task:** … · **State:** … · **Next step:** …
   - **Drift:** (only if any) · **Open questions:** (only if any)
   - Then ask: continue with the next step, or pick something from the backlog?
