---
name: timesheet
description: Turn tracked Claude-session activity per repo into TDC timesheet hours and log them after confirmation. Use for /timesheet, "log my time", "what did I work on today", "fill my timesheet".
---

# /timesheet: tracked activity → TDC timesheet

> **Paths:** `<kit>` below means this plugin's root folder: two levels up from this skill's base directory (the folder holding this SKILL.md) (`<kit>/scripts`, `<kit>/template`).


Hooks (in Claude Code and in Codex) record activity timing and the repo path. The legacy/default
backend is `~/.claude/timesheet/activity.log`. An opt-in SQLite backend stores sanitised tool
categories and edited file names as well, allowing locally reviewed work summaries. It does not
store prompts, file contents, command text, tool output, or transcripts. `clients.tsv` maps path
prefixes to timesheet client names. `<kit>/scripts/ts.py summary` turns the activity into active
hours per client.

`view_timesheet`, `log_time`, `list_timesheet_clients`, `list_timesheet_client_catalogue` and
`timesheet_next_gap` come from the TDC timesheet connector. If they aren't available (e.g. in Codex
without that MCP server set up), show the proposal table and tell the user to log it in the timesheet
themselves.

**This is Claude/Codex-active time only.** Meetings, calls, testing on a phone and work outside
Claude don't show up. Always say so and ask the user what to add.

## Steps

1. **Pick the date.** Use the one the user named, otherwise today. If they say "catch up", call
   `timesheet_next_gap` and work through each unfilled weekday in turn.
2. **Synchronise the client catalogue (SQLite only):** call
   `list_timesheet_client_catalogue`, start
   `python3 <kit>/scripts/ts.py clients --import - --replace`, and write the exact catalogue JSON
   to that process's standard input. This stores client names and stable GUIDs locally; it does not
   upload activity. If the catalogue tool is unavailable, continue with review but explain that
   activities may show `GUID pending` and cannot be uploaded yet.
3. **Review local work (SQLite only):** run
   `python3 <kit>/scripts/ts.py review-ui` and tell the user that the browser page saves directly to
   the private local SQLite database. It shows all retained activity across all dates until each
   item is uploaded or explicitly deleted. The user may edit, split, merge, approve, mark private,
   exclude, filter by status, upload approved activities, or delete excluded/private/uploaded items.
   `Upload Activities` sends all approved activities directly to TDC Internal; the first use opens
   the normal browser sign-in and then resumes automatically. Never approve, upload, or delete an
   activity on the user's behalf. Wait for the user to say review is complete, then run
   `python3 <kit>/scripts/ts.py review-status --all --json`.
   If a browser cannot be opened, fall back to `review <date> --json` and the existing CLI edits.
   If SQLite is not enabled, continue with the legacy flow below.
4. **Summarise:** run `python3 <kit>/scripts/ts.py summary <date> --json`. Approved review entries
   are the descriptions and blocks the user chose; the summary remains the allocation calculation.
5. **Unmapped paths:** call `list_timesheet_clients`, suggest a client for each unmapped path,
   ask the user to confirm, then save it with `python3 <kit>/scripts/ts.py map <prefix> "<Client>"` (map the top project
   folder, not a deep subfolder) and re-run the summary.
6. **Read what's already logged:** call `view_timesheet` for that date.
   ⚠️ `log_time` **replaces** the whole day, so the final allocation must include the existing
   entries. Merge tracked hours into them and never double-count a client already logged.
7. **Propose** a table: client · tracked hours · already logged · proposed hours, plus a total.
   Hours are rounded to 0.25. Flag these:
   - `overlap_hours` > 0 (parallel sessions on different clients). Ask how to split it.
   - total under ~6h on a full workday. Ask what non-Claude work to add.
   - total over ~10h. Looks wrong, so check with the user.
   Include the approved local work summaries where available. The current timesheet connector stores
   allocations, not descriptions; the SQLite review remains the local record of those descriptions.
8. **Confirm before logging.** Local activity approval is not permission to replace the online
   timesheet. The user separately approves or edits the final allocation. Only then call
   `log_time(date, allocations=[{clientName, hours}, …])` with the full merged day.
9. Report what was logged. Activity upload and online timesheet logging remain separate actions:
   `Upload Activities` records the reviewed descriptions in TDC Internal, while `log_time` replaces
   the day's timesheet allocation only after the separate confirmation in step 8.
