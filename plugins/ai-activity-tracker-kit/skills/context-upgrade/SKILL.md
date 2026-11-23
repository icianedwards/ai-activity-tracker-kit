---
name: context-upgrade
description: Roll the latest ai-activity-tracker-kit changes into every .context/ folder on this machine (README rules, .gitignore, new template files, version), without touching anyone's notes. Use for /context-upgrade, "upgrade my context folders", "apply the kit update to all repos", after updating the plugin.
---

# /context-upgrade: apply the current kit to all `.context/` folders

> **Paths:** `<kit>` below means this plugin's root folder: two levels up from this skill's base directory (the folder holding this SKILL.md).

The kit owns `.context/README.md` and `.context/.gitignore`. Everything else in `.context/`
belongs to the people who write it, so this only **adds** missing template files and never
overwrites notes.

## Steps
1. **Get the latest kit first**, unless the user just did: Claude Code `/plugin marketplace update tdc`,
   Codex `codex plugin marketplace upgrade`. Restart the session if the version changed.
2. **Root:** the folder the user names; otherwise their dev root (ask once if unclear, e.g.
   `~/Documents/DEVELOPMENT`).
3. **Dry run:** `python3 <kit>/scripts/upgrade.py <root>`. Show the user a short summary:
   how many folders, what would change, and every `note:` line.
4. On their OK: `python3 <kit>/scripts/upgrade.py <root> --apply`.
5. **Follow the notes**, with the user's OK for each:
   - *No `@.context/INDEX.md` import*: offer to add the pointer section from `/ctx-init` to that CLAUDE.md.
   - *AGENTS.md doesn't mention `.context/`*: offer to add the Codex pointer section from `/ctx-init`.
   - *Over budget*: offer to split or trim the file (same rules as `/handoff`).
6. **Git:** repos that print `git: .context changes to commit` need a commit. Don't commit unless
   asked; list them. Project-level folders aren't in git.
7. If the kit's `CHANGELOG.md` entry for this version asks for manual steps, do them or list them.
