# ICE AI Activity Tracker Kit

Shared Claude Code and Codex plugins for ICE. See [Contributing](#contributing).

## ai-activity-tracker-kit

Session memory per repo and per project, so a new Claude or Codex session starts from a short summary instead of re-reading the codebase. It also tracks activity per repo for the TDC timesheet.

| Command | What it does |
|---|---|
| `/ctx-init` | Create `.context/` in a repo, or `--project` level in a client folder that holds several repos |
| `/handoff` | End of task: write what happened into `.context/` (NOW, backlog, notes, decisions, history) |
| `/pickup` | Start of session: short brief from `.context/` plus git drift (usually automatic) |
| `/context-upgrade` | Apply the latest kit to every `.context/` folder on your machine |
| `/timesheet` | Turn tracked activity into TDC timesheet hours (asks before logging) |

Plugin commands may show namespaced as `/ai-activity-tracker-kit:handoff`. In Codex, type `$` instead of `/` (`$handoff`).

**Hooks** (automatic): at session start, load the project and repo context. Every prompt and tool call, plus turn ends, log activity for the timesheet (tool calls at most one line a minute). At session end, save a snapshot if `/handoff` wasn't run.

### How `.context/` works
```
<client>/.context/          project level: across repos (how they connect, shared DB, product backlog)
<client>/<repo>/.context/   repo level: how this code works
  INDEX.md  current/NOW.md  system/  conventions/  todo/  decisions/  history/
```
Each fact lives at one level only. `current/` and `history/` are personal and gitignored; the rest is committed and shared. Full rules: [`template/README.md`](plugins/ai-activity-tracker-kit/template/README.md).

## Install

**Pick one route, never both** (both = every hook runs twice):
- **GitHub (recommended)**: always the latest version, and you can contribute. Steps below.
- **Remote GitHub repository**: install directly from the personal repository below.

1. Add the marketplace and install the plugin:
   ```
   /plugin marketplace add icianedwards/ai-activity-tracker-kit
   /plugin install ai-activity-tracker-kit@ice
   ```
   You need read access to this repo.
2. Restart Claude Code.
3. In a repo you work in a lot, run `/ctx-init`.
4. Timesheet tracking needs the **TDC Timesheet** connector. The first `/timesheet` asks which client each of your folders belongs to, and saves that to `~/.claude/timesheet/clients.tsv` on your machine only.

### Local SQLite activity review

TSV remains the default storage format. SQLite can be enabled locally without changing or migrating
the existing TSV log. Create `~/.claude/timesheet-config.json`:

```json
{
  "home": "~/.claude/timesheet-sqlite-trial",
  "storage": "sqlite"
}
```

New hook activity then goes to `activity.db` in the trial folder. Existing
`~/.claude/timesheet/activity.log` and `clients.tsv` are not modified. Copy `clients.tsv` to the
trial folder if existing client mappings should be reused.

The SQLite backend stores timestamps, the source agent, a hashed session identifier, the local
workspace, a safe tool category, and relative names of files edited by edit tools. It never stores
prompts, file contents, command text, tool output, or transcripts. The database and its parent folder
are restricted to the current operating-system user.

Useful local commands:

```bash
# Open the local review application for all retained activity. It binds only to 127.0.0.1
# and uses a random token.
python3 plugins/ai-activity-tracker-kit/scripts/ts.py review-ui

# The command-line review remains available for automation.
python3 plugins/ai-activity-tracker-kit/scripts/ts.py review --id 1 \
  --client "TDC Internal" --client-id "00000000-0000-0000-0000-000000000000" \
  --summary "Implemented local SQLite activity review" --status approved

# Import the active client catalogue, including the stable API client GUIDs.
python3 plugins/ai-activity-tracker-kit/scripts/ts.py clients --import clients.json --replace

# The catalogue can also be passed directly from the TDC Internal MCP result on stdin.
python3 plugins/ai-activity-tracker-kit/scripts/ts.py clients --import - --replace

# Approve activities in the browser and click Upload Activities. The first upload opens the
# existing TDC Internal sign-in; later uploads reuse the local refresh token.

# Export approved entries only (default), as JSON or CSV.
python3 plugins/ai-activity-tracker-kit/scripts/ts.py export --format json
python3 plugins/ai-activity-tracker-kit/scripts/ts.py export --format csv --output ~/Desktop/timesheet.csv

# Import a legacy log without modifying its source file. Re-running is idempotent.
python3 plugins/ai-activity-tracker-kit/scripts/ts.py import-tsv ~/.claude/timesheet/activity.log
```

Activity review statuses are `pending`, `approved`, `private`, and `excluded`. Clicking Upload
Activities sends approved entries immediately. Successful entries become `uploaded`; failures become
`failed` and remain available for retry. Export includes only `approved` entries unless `--status`
is supplied explicitly.

The browser UI writes changes directly to SQLite, so closing or reopening it does not lose review
work. Editing approved content returns it to `pending` and requires approval again. Raw events remain
local and are never uploaded as individual records.

Python 3 and git must be on your PATH (macOS has both).

### Codex

ai-activity-tracker-kit also runs in OpenAI Codex (0.2.0+): same skills, hooks and `.context/` folders. Codex support is new and lightly tested; report anything odd.

1. Add the marketplace:
   ```bash
   codex plugin marketplace add icianedwards/ai-activity-tracker-kit
   ```
2. Install **ai-activity-tracker-kit** from the Plugins directory in the Codex desktop app, or enable it in `~/.codex/config.toml`:
   ```toml
   [plugins."ai-activity-tracker-kit@ice"]
   enabled = true
   ```
3. If Codex shows a hook trust prompt, approve the ai-activity-tracker-kit hooks. The desktop app does not expose a `/hooks` command-menu entry; trust is requested automatically when required.
4. Codex reads `AGENTS.md`, not `CLAUDE.md`. `$ctx-init` adds a `.context/` pointer to `AGENTS.md`; `$context-upgrade` flags repos where it's missing.
5. `$timesheet` uses the same `~/.claude/timesheet/` log as Claude Code, so time from both tools adds up in one place. Logging hours needs the TDC Timesheet MCP server added to Codex; without it, `$timesheet` shows the proposal and you enter it yourself.

Updates: `codex plugin marketplace upgrade`, then restart Codex.

## Getting updates

```
/plugin marketplace update ice
```
Then restart, and run `/context-upgrade` to apply template changes to the `.context/` folders you already have. Only the kit-owned files (`.context/README.md` and `.gitignore`) are replaced; your notes are never overwritten.

## Contributing

1. Branch, change, and open a PR. Keep changes small.
2. **Bump `version`** in both `plugins/ai-activity-tracker-kit/.claude-plugin/plugin.json` and `.codex-plugin/plugin.json` (same number; `0.2.0` → `0.2.1` for fixes, `0.3.0` for new behaviour). Without a bump, people's installs won't pick up the change.
3. Add a line to [`CHANGELOG.md`](CHANGELOG.md). If people need to do something after updating, say so there: `/context-upgrade` shows it.
4. Test locally before merging:
   ```bash
   claude plugin validate plugins/ai-activity-tracker-kit
   claude --plugin-dir plugins/ai-activity-tracker-kit
   ```
5. No personal data in the kit: no names, emails, machine paths, or client mappings.
6. After merging a version bump, the person who first published it re-publishes to the claude.ai library
   (in Claude Code: "publish plugins/ai-activity-tracker-kit to the org"), so library users get it too.

| Path | What it is |
|---|---|
| `plugins/ai-activity-tracker-kit/skills/` | The commands (one `SKILL.md` each) |
| `.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json` | Marketplaces for Claude Code and Codex |
| `plugins/ai-activity-tracker-kit/hooks/` | `hooks.json`, start-up loader, end-of-session snapshot |
| `plugins/ai-activity-tracker-kit/scripts/` | `scaffold.sh`, `upgrade.py`, `ts.py` (timesheet tracker) |
| `plugins/ai-activity-tracker-kit/template/` | What `/ctx-init` copies into a repo (`template-project/` overrides INDEX for project level) |
