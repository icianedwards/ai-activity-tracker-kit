# Changelog

Newest first. Note any manual step people need after updating.

## ai-activity-tracker-kit 0.5.7 — 2026-11-23
- Codex now uses the dedicated `codex-hooks.json` manifest so local activity is recorded with `source = codex` instead of relying on stale shared hook registration.
- Bump the local plugin version so Codex reloads and trusts the updated hook manifest.

## ai-activity-tracker-kit 0.5.6 — 2026-11-15
- Added a dedicated `Activity Review` skill that starts and opens the local activity review and upload page.

## ai-activity-tracker-kit 0.5.5 — 2026-11-15
- Plain-text local server errors are now normalised into full technical error detail instead of causing a secondary traceback-rendering failure.

## ai-activity-tracker-kit 0.5.4 — 2026-11-15
- Error dialogs now include a Copy details button that copies the readable message and full technical detail for pasting into Claude or Codex.

## ai-activity-tracker-kit 0.5.3 — 2026-11-15
- Every error dialog now shows all available technical detail, including local Python or browser JavaScript stack traces, with the detail section expanded by default.

## ai-activity-tracker-kit 0.5.2 — 2026-11-15
- Error dialogs now include expanded technical detail: exception type, HTTP request endpoint, status and the API response body.

## ai-activity-tracker-kit 0.5.1 — 2026-11-15
- Error messages now use a persistent, blocking in-page alert dialog instead of a temporary corner notification.

## ai-activity-tracker-kit 0.5.0 — 2026-11-15
- Replaced the staged Prepare flow with one `Upload Activities` action that sends approved work directly to TDC Internal.
- Added one-time browser OAuth sign-in with refresh-token reuse, automatic return to the review page, immediate success/failure state, and retry support.
- Removed submission snapshots from the review UI; uploaded activities can still be deleted locally with `Delete Uploaded`.

## ai-activity-tracker-kit 0.4.4 — 2026-11-15
- Replaced browser confirmation prompts with accessible, styled in-page modals for split, merge, preparation and deletion actions.

## ai-activity-tracker-kit 0.4.3 — 2026-11-15
- Review filters and actions now use separate header rows for a cleaner layout.

## ai-activity-tracker-kit 0.4.2 — 2026-11-15
- Bulk deletion buttons are now hidden when their matching activity count is zero.

## ai-activity-tracker-kit 0.4.1 — 2026-11-15
- The review status filter now omits statuses with no matching local activities.

## ai-activity-tracker-kit 0.4.0 — 2026-11-15
- Local review now displays retained activities across every date instead of filtering to one day.
- Added a multi-status filter and lifecycle states for prepared, uploading, failed and uploaded submissions.
- Added server-validated `Delete Excluded`, `Delete Private` and `Delete Uploaded` actions with live counts. Deletion removes the activity, its submission/review records and unshared raw evidence so deleted work does not reappear.
- Removed per-field upload inclusion controls from the review cards. The established safe payload defaults remain in effect.

## ai-activity-tracker-kit 0.3.3 — 2026-11-15
- The review client dropdown now flags only clients whose GUID is missing; synchronised clients no longer carry a redundant status label.

## ai-activity-tracker-kit 0.3.2 — 2026-11-15
- The timesheet workflow now synchronises the active TDC Internal client catalogue, including stable client GUIDs, before local review.
- Client import accepts both camel-case and .NET-style catalogue property names and backfills matching local activities and workspace mappings.
- Review now distinguishes an unassigned client from a selected client whose GUID has not been synchronised.

## ai-activity-tracker-kit 0.3.1 — 2026-11-15
- Codex and Claude Code now use separate hook manifests that explicitly identify their source agent. This avoids activity being recorded as `unknown` when the host does not expose an app-specific environment variable to hook processes.
- Codex desktop does not require a `/hooks` command-menu entry for this update. Existing trusted hooks continue running; new or changed hooks are handled by Codex's normal trust prompt.

## ai-activity-tracker-kit 0.3.0 — 2026-11-15
- **Local activity review UI.** `ts.py review-ui [YYYY-MM-DD]` opens a token-protected localhost page backed directly by the private SQLite database. Reviewers can edit summaries, clients and projects; choose optional upload fields; approve, exclude or mark work private; and split or merge activity blocks.
- SQLite now records the source agent (`codex`, `claude-code`, `legacy` or `unknown`) and keeps versioned activity segments, review revisions, stable client IDs, payload hashes and immutable submission snapshots.
- New commands: `clients`, `review-status`, `preview-upload`, `prepare-upload` and `submissions`. Preparing a payload never sends it; upload attempts and API receipts can be tracked independently as `prepared`, `uploading`, `uploaded` or `failed`.
- Existing TSV logs, raw SQLite events and legacy review rows remain intact. SQLite users can update in place; the additive schema is created automatically on first use.

## ai-activity-tracker-kit 0.2.0 — 2026-11-13
- **Codex support.** The same plugin installs in OpenAI Codex (`codex plugin marketplace add icianedwards/ai-activity-tracker-kit`): skills (`$handoff` etc.), start-up context, end-of-session snapshot and timesheet tracking.
- Timesheet: Codex `apply_patch` edits count for the edited file's repo, like Claude Code edits. Claude Code and Codex share one activity log.
- Start-up loader: in Codex it always injects INDEX (AGENTS.md has no imports) and names commands with `$`. The end-of-session snapshot fits Codex's 3-second limit.
- `$ctx-init` / `/ctx-init` adds a `.context/` pointer to `AGENTS.md`; `/context-upgrade` notes repos whose `AGENTS.md` lacks one.
- **Codex users:** run `/hooks` and trust the ai-activity-tracker-kit hooks after installing or updating.

## ai-activity-tracker-kit 0.1.1 — 2026-11-13
- Timesheet tracking logs the folder the session is working in (the hook's `cwd`, which follows `cd`), not the folder it was opened in. Edits to files in an added directory count for that file's repo. Sessions that moved between repos were billed entirely to the first one.
- End-of-session snapshot covers both the repo the session ended in and the one it was opened in; the start-up loader uses the current folder after `/clear`, `/compact` or a resume.
- No manual step. Activity logged before this version keeps its old attribution.

## ai-activity-tracker-kit 0.1.0 — 2026-11-13
- First shared version: `/ctx-init` (repo and `--project` level), `/handoff`, `/pickup`, `/context-upgrade`, `/timesheet`.
- Hooks: load project + repo context at start (worktrees use the main checkout's INDEX), snapshot at end if no `/handoff`, activity tracking for the timesheet (tool calls at most one line a minute).
- **After installing:** if you had personal copies of these skills or hooks in `~/.claude/`, remove them so nothing runs twice.
