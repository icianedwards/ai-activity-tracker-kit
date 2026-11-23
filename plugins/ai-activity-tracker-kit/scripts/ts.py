#!/usr/bin/env python3
"""Track Claude Code / Codex session activity per repo and roll it up into TDC timesheet hours.

  ts.py hook                              capture a Claude Code / Codex hook event
  ts.py summary [YYYY-MM-DD] [--json]    active hours per client for a day
  ts.py map <path-prefix> <client>       add/replace a path -> client mapping
  ts.py import-tsv [activity.log]        import a legacy TSV log into SQLite
  ts.py review [YYYY-MM-DD] [--json]     list local work entries awaiting review
  ts.py review --id N [changes]          edit status, client, or summary
  ts.py review-ui                        open the all-activity SQLite review page
  ts.py review-status [YYYY-MM-DD]       show locally reviewed activity state
  ts.py clients [--import FILE]          manage stable client identifiers
  ts.py preview-upload [YYYY-MM-DD]      show exact approved upload payloads
  ts.py prepare-upload [YYYY-MM-DD]      confirm and queue immutable payload snapshots
  ts.py submissions                      inspect or update local upload attempts
  ts.py export --format json|csv         export approved work entries

Active time = gaps between consecutive events (any session) of <= GAP_MIN minutes,
plus TAIL_MIN after the last event in each burst (reading/testing Claude's output).
Intervals are unioned per client, so parallel sessions on one client don't double count.
TSV remains the default. Set storage to sqlite in ~/.claude/timesheet-config.json to opt in.
"""
import csv, hashlib, io, json, os, re, sqlite3, subprocess, sys, time
from datetime import datetime, timedelta

import review_store


def load_bootstrap_config():
    path = os.environ.get("TS_CONFIG") or os.path.expanduser("~/.claude/timesheet-config.json")
    try:
        with open(path, encoding="utf-8") as stream:
            value = json.load(stream)
            return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


BOOTSTRAP_CONFIG = load_bootstrap_config()
HOME = os.path.expanduser(os.environ.get("TS_HOME") or BOOTSTRAP_CONFIG.get("home", "~/.claude/timesheet"))
LOG = os.path.join(HOME, "activity.log")
MAP = os.path.join(HOME, "clients.tsv")
DB = os.path.join(HOME, "activity.db")
STORAGE = str(os.environ.get("TS_STORAGE") or BOOTSTRAP_CONFIG.get("storage", "tsv")).strip().lower()
GAP_MIN = 15   # idle gap that ends a burst of work
TAIL_MIN = 5   # credit after the last event in a burst
ROUND_H = 0.25


def load_map():
    m = []
    if os.path.exists(MAP):
        for line in open(MAP, encoding="utf-8"):
            if line.strip() and not line.startswith("#") and "\t" in line:
                p, c = line.rstrip("\n").split("\t", 1)
                m.append((p.rstrip("/"), c.strip()))
    return sorted(m, key=lambda x: -len(x[0]))


def client_for(path, m):
    for p, c in m:
        if path == p or path.startswith(p + "/"):
            return c
    return None


EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}   # Claude Code
PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$|^\*\*\* Move to: (.+)$", re.M)
TOOL_ACTIONS = {
    "Edit": "edit", "MultiEdit": "edit", "Write": "edit", "NotebookEdit": "edit",
    "apply_patch": "edit", "Read": "review", "Glob": "review", "Grep": "review",
    "find": "review", "view_image": "review", "Bash": "command", "exec_command": "command",
    "write_stdin": "command", "WebSearch": "research", "WebFetch": "research", "web__run": "research",
}


def git_root(path):
    try:
        r = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=2)
        return r.stdout.strip() or None
    except Exception:
        return None


def edited_files(d, cwd):
    """Absolute paths of files this tool call wrote, if it was an edit."""
    ti = d.get("tool_input") or {}
    if d.get("tool_name") in EDIT_TOOLS:
        files = [ti.get("file_path") or ti.get("notebook_path")]
    elif d.get("tool_name") == "apply_patch":   # Codex: file names are inside the patch text
        cmd = ti.get("command") or ti.get("input") or ""
        if isinstance(cmd, list):
            cmd = "\n".join(map(str, cmd))
        files = [a or b for a, b in PATCH_FILE.findall(str(cmd))]
    else:
        return []
    return [os.path.join(cwd, f.strip()) for f in files if f and f.strip()]


def work_path(d):
    """Folder the session is working in right now.

    The hook input's `cwd` follows the session's working directory (Claude Code: it changes
    after a `cd` into the project or an added directory); CLAUDE_PROJECT_DIR stays at the
    folder the session was opened in (and Codex has no such variable), so it is only a
    fallback. An edit to a file outside `cwd` counts for that file's repo.
    """
    path = d.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    prefix = path.rstrip("/") + "/"
    for f in edited_files(d, path):
        f = os.path.normpath(f)
        if f != path and not f.startswith(prefix):
            parent = os.path.dirname(f)
            return git_root(parent) or parent
    return path


def last_line():
    try:
        with open(LOG, "rb") as f:
            f.seek(0, 2); f.seek(max(0, f.tell() - 4096))
            return f.read().decode("utf-8", "replace").rstrip("\n").split("\n")[-1].split("\t")
    except OSError:
        return []


def open_db():
    os.makedirs(HOME, mode=0o700, exist_ok=True)
    try:
        os.chmod(HOME, 0o700)
    except OSError:
        pass
    db = sqlite3.connect(DB, timeout=0.5)
    try:
        os.chmod(DB, 0o600)
    except OSError:
        pass
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=500")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS activity_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at INTEGER NOT NULL,
            event_name TEXT NOT NULL,
            session_key TEXT NOT NULL,
            workspace_path TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'unknown',
            tool_name TEXT,
            edited_files_json TEXT NOT NULL DEFAULT '[]',
            event_hash TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_activity_events_day
            ON activity_events(occurred_at, workspace_path);
        CREATE TABLE IF NOT EXISTS work_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_date TEXT NOT NULL,
            workspace_path TEXT NOT NULL,
            client TEXT,
            client_is_manual INTEGER NOT NULL DEFAULT 0,
            started_at INTEGER NOT NULL,
            ended_at INTEGER NOT NULL,
            duration_seconds INTEGER NOT NULL,
            summary TEXT NOT NULL,
            summary_is_manual INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            UNIQUE(work_date, workspace_path)
        );
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_entry_id INTEGER NOT NULL,
            target TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            submitted_at INTEGER NOT NULL,
            FOREIGN KEY(work_entry_id) REFERENCES work_entries(id)
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(activity_events)")}
    if "event_hash" not in columns:
        db.execute("ALTER TABLE activity_events ADD COLUMN event_hash TEXT")
    if "source" not in columns:
        db.execute("ALTER TABLE activity_events ADD COLUMN source TEXT NOT NULL DEFAULT 'unknown'")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_activity_events_hash ON activity_events(event_hash)")
    work_columns = {row[1] for row in db.execute("PRAGMA table_info(work_entries)")}
    if "client_is_manual" not in work_columns:
        db.execute("ALTER TABLE work_entries ADD COLUMN client_is_manual INTEGER NOT NULL DEFAULT 0")
    review_store.ensure_schema(db)
    return db


def session_key(session_id):
    return hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()


def safe_edited_files(d, workspace):
    safe = []
    workspace = os.path.normpath(workspace)
    for path in edited_files(d, workspace):
        path = os.path.normpath(path)
        try:
            relative = os.path.relpath(path, workspace)
        except ValueError:
            relative = os.path.basename(path)
        if relative == ".." or relative.startswith(".." + os.sep):
            relative = os.path.basename(path)
        relative = relative.replace(os.sep, "/")[:240]
        if relative and relative not in safe:
            safe.append(relative)
        if len(safe) == 10:
            break
    return safe


def safe_tool_action(tool_name):
    name = str(tool_name or "")
    return TOOL_ACTIONS.get(name) or TOOL_ACTIONS.get(name.rsplit("__", 1)[-1])


def detect_agent_source(d, explicit=None):
    """Identify the local agent without retaining a raw session or transcript."""
    candidates = [
        explicit,
        d.get("agent_source"),
        d.get("source_agent"),
        d.get("client_name"),
        d.get("application"),
    ]
    for value in candidates:
        normalised = str(value or "").strip().lower().replace("_", "-")
        if normalised in {"codex", "openai-codex", "codex-desktop"}:
            return "codex"
        if normalised in {"claude", "claude-code", "anthropic-claude"}:
            return "claude-code"
        if normalised == "legacy":
            return "legacy"
    if os.environ.get("CODEX_SESSION_ID") or os.environ.get("CODEX_THREAD_ID"):
        return "codex"
    if os.environ.get("CLAUDE_PROJECT_DIR") or os.environ.get("CLAUDE_PLUGIN_ROOT"):
        return "claude-code"
    return "unknown"


def write_sqlite_event(d, explicit_source=None):
    now = int(time.time())
    path = work_path(d)[:1024]
    key = session_key(d.get("session_id", "?"))
    event_name = str(d.get("hook_event_name", "?"))[:80]
    source = detect_agent_source(d, explicit_source)
    tool_name = safe_tool_action(d.get("tool_name"))
    files = safe_edited_files(d, path)
    with open_db() as db:
        if event_name == "PostToolUse":
            recent = db.execute(
                """SELECT occurred_at FROM activity_events
                   WHERE session_key = ? AND workspace_path = ?
                   ORDER BY occurred_at DESC LIMIT 1""",
                (key, path),
            ).fetchone()
            if recent and now - recent["occurred_at"] < 60:
                return
        db.execute(
            """INSERT INTO activity_events
               (occurred_at, event_name, session_key, workspace_path, source, tool_name, edited_files_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (now, event_name, key, path, source, tool_name, json.dumps(files)),
        )


def sqlite_hook(d, explicit_source=None):
    for attempt in range(3):
        try:
            write_sqlite_event(d, explicit_source)
            return
        except (OSError, sqlite3.Error) as error:
            if attempt < 2:
                time.sleep(0.05 * (attempt + 1))
                continue
            print(f"ai-activity-tracker-kit: dropped timesheet event after SQLite failure: {error}", file=sys.stderr)


def cmd_hook(args=None):
    try:
        d = json.load(sys.stdin)
    except Exception:
        d = {}
    explicit_source = arg_value(args or [], "--source")
    if STORAGE == "sqlite":
        sqlite_hook(d, explicit_source)
        return
    os.makedirs(HOME, exist_ok=True)
    path = work_path(d)
    sid = d.get("session_id", "?")
    # Tool calls come in bursts: one line a minute per session and folder is plenty.
    if d.get("hook_event_name") == "PostToolUse":
        last = last_line()
        if len(last) == 4 and last[0].isdigit() and last[2] == sid and last[3] == path \
                and time.time() - int(last[0]) < 60:
            return
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{int(time.time())}\t{d.get('hook_event_name','?')}\t{sid}\t{path}\n")


def generated_summary(workspace, events):
    files = []
    for event in events:
        for path in json.loads(event["edited_files_json"] or "[]"):
            if path not in files:
                files.append(path)
    if files:
        shown = files[:3]
        suffix = f" and {len(files) - len(shown)} more" if len(files) > len(shown) else ""
        return f"Updated {', '.join(shown)}{suffix}"
    actions = {event["tool_name"] for event in events if event["tool_name"]}
    if "edit" in actions:
        return "Updated project files"
    if "review" in actions:
        return "Reviewed project files"
    if "research" in actions:
        return "Researched project context"
    if "command" in actions:
        return "Ran local project commands"
    return f"Worked in {os.path.basename(workspace.rstrip(os.sep)) or 'project'}"


def sync_work_entries(day):
    start = int(datetime.strptime(day, "%Y-%m-%d").timestamp())
    end = start + 86400
    mapping = load_map()
    with open_db() as db:
        events = db.execute(
            """SELECT * FROM activity_events
               WHERE occurred_at >= ? AND occurred_at < ?
               ORDER BY occurred_at""",
            (start, end),
        ).fetchall()
        grouped = {}
        for event in events:
            grouped.setdefault(event["workspace_path"], []).append(event)
        now = int(time.time())
        for workspace, workspace_events in grouped.items():
            times = [event["occurred_at"] for event in workspace_events]
            worked = intervals(times)
            started_at = worked[0][0]
            ended_at = worked[-1][1]
            duration = int(total(worked))
            summary = generated_summary(workspace, workspace_events)
            client = client_for(workspace, mapping)
            db.execute(
                """INSERT INTO work_entries
                   (work_date, workspace_path, client, started_at, ended_at, duration_seconds,
                    summary, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                   ON CONFLICT(work_date, workspace_path) DO UPDATE SET
                     client = CASE WHEN work_entries.client_is_manual = 1
                                   THEN work_entries.client ELSE excluded.client END,
                     started_at = excluded.started_at,
                     ended_at = excluded.ended_at,
                     duration_seconds = excluded.duration_seconds,
                     summary = CASE WHEN work_entries.summary_is_manual = 1
                                    THEN work_entries.summary ELSE excluded.summary END,
                     updated_at = excluded.updated_at""",
                (day, workspace, client, started_at, ended_at, duration, summary, now, now),
            )


def review_entries(day):
    with open_db() as db:
        review_store.sync_review_activities(db, day, load_map(), GAP_MIN, TAIL_MIN)
        activities = review_store.list_activities(db, day)
    return [{
        "id": activity["id"],
        "activity_key": activity["activityKey"],
        "date": activity["date"],
        "workspace": activity["workspace"],
        "workspace_key": activity["workspaceKey"],
        "client_id": activity["clientId"],
        "client": activity["clientName"],
        "project": activity["projectName"],
        "source": activity["source"],
        "first": activity["first"],
        "last": activity["last"],
        "active_minutes": activity["activeMinutes"],
        "hours_raw": round(activity["activeMinutes"] / 60, 2),
        "hours": rnd(activity["activeMinutes"] / 60),
        "summary": activity["summary"],
        "status": activity["status"],
        "segments": activity["segments"],
        "inclusion": activity["inclusion"],
        "payload_hash": activity["payloadHash"],
        "client_guid_missing": activity["clientGuidMissing"],
    } for activity in activities]


def cmd_review(args):
    if STORAGE != "sqlite":
        sys.exit("review requires TS_STORAGE=sqlite")
    day = next((a for a in args if a[:1].isdigit()), datetime.now().strftime("%Y-%m-%d"))
    entry_id = arg_value(args, "--id")
    if entry_id:
        status = arg_value(args, "--status")
        summary = arg_value(args, "--summary")
        client = arg_value(args, "--client")
        client_id = arg_value(args, "--client-id")
        project = arg_value(args, "--project")
        if status and status not in {"pending", "approved", "private", "excluded"}:
            sys.exit("status must be pending, approved, private, or excluded")
        changes = {}
        if summary is not None:
            changes["summary"] = summary
        if client is not None:
            changes["clientName"] = client[:200] or None
        if client_id is not None:
            changes["clientId"] = client_id or None
        if project is not None:
            changes["projectName"] = project
        try:
            with open_db() as db:
                row = db.execute(
                    "SELECT work_date FROM review_activities WHERE id = ?", (int(entry_id),)
                ).fetchone()
                if not row:
                    sys.exit(f"work entry #{entry_id} was not found")
                day = row["work_date"]
                if changes:
                    review_store.update_activity(db, int(entry_id), changes, "timesheet-cli")
                if status:
                    review_store.set_activity_status(db, int(entry_id), status, "timesheet-cli")
        except (KeyError, ValueError) as error:
            sys.exit(str(error))
    entries = review_entries(day)
    if "--json" in args:
        print(json.dumps({"date": day, "entries": entries}, indent=2))
        return
    print(f"Local work review {day}")
    for entry in entries:
        client = entry["client"] or "UNMAPPED"
        print(f"  #{entry['id']} [{entry['status']}] {entry['first']}-{entry['last']} "
              f"{entry['hours']:.2f}h  {client}  {entry['summary']}")


def arg_value(args, name, default=None):
    try:
        return args[args.index(name) + 1]
    except (ValueError, IndexError):
        return default


def cmd_export(args):
    if STORAGE != "sqlite":
        sys.exit("export requires TS_STORAGE=sqlite")
    output_format = arg_value(args, "--format", "json").lower()
    if output_format not in {"json", "csv"}:
        sys.exit("export format must be json or csv")
    status = arg_value(args, "--status", "approved")
    if status not in {"approved", "pending", "private", "excluded", "all"}:
        sys.exit("export status must be approved, pending, private, excluded, or all")
    day = next((a for a in args if a[:1].isdigit()), None)
    review_entries(day or datetime.now().strftime("%Y-%m-%d"))
    with open_db() as db:
        query = "SELECT work_date FROM review_activities"
        clauses, values = [], []
        if day:
            clauses.append("work_date = ?")
            values.append(day)
        if status != "all":
            clauses.append("status = ?")
            values.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        dates = [row[0] for row in db.execute(query + " GROUP BY work_date ORDER BY work_date", values)]
    entries = []
    for entry_day in dates:
        entries.extend(entry for entry in review_entries(entry_day)
                       if status == "all" or entry["status"] == status)
    if output_format == "json":
        content = json.dumps({"entries": entries}, indent=2) + "\n"
    else:
        stream = io.StringIO()
        fields = [
            "id", "activity_key", "date", "workspace", "workspace_key", "client_id", "client",
            "project", "source", "first", "last", "active_minutes", "hours_raw", "hours",
            "summary", "status", "segments", "inclusion", "payload_hash", "client_guid_missing",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(entries)
        content = stream.getvalue()
    output_path = arg_value(args, "--output")
    if output_path:
        with open(os.path.abspath(os.path.expanduser(output_path)), "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
        print(f"Exported {len(entries)} work entr{'y' if len(entries) == 1 else 'ies'} to {output_path}.")
    else:
        print(content, end="")


def read_events(day):
    start = datetime.strptime(day, "%Y-%m-%d").timestamp()
    end = start + 86400
    ev = []
    if STORAGE == "sqlite":
        with open_db() as db:
            rows = db.execute(
                """SELECT occurred_at, event_name, session_key, workspace_path
                   FROM activity_events
                   WHERE occurred_at >= ? AND occurred_at < ?""",
                (int(start), int(end)),
            ).fetchall()
        ev.extend((row[0], row[1], row[2], row[3]) for row in rows)
    elif os.path.exists(LOG):
        for line in open(LOG, encoding="utf-8"):
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 4 and parts[0].isdigit():
                t = int(parts[0])
                if start <= t < end:
                    ev.append((t, parts[1], parts[2], parts[3]))
    return sorted(ev)


def cmd_import_tsv(args):
    if STORAGE != "sqlite":
        sys.exit("import-tsv requires TS_STORAGE=sqlite")
    source = os.path.abspath(os.path.expanduser(args[0] if args else LOG))
    if not os.path.exists(source):
        sys.exit(f"TSV activity log not found: {source}")
    imported = skipped = invalid = 0
    with open_db() as db, open(source, encoding="utf-8") as stream:
        for line in stream:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 4 or not parts[0].isdigit():
                invalid += 1
                continue
            occurred_at, event_name, raw_session, workspace = parts
            digest = hashlib.sha256(line.rstrip("\n").encode("utf-8")).hexdigest()
            cursor = db.execute(
                """INSERT OR IGNORE INTO activity_events
                   (occurred_at, event_name, session_key, workspace_path, source, event_hash)
                   VALUES (?, ?, ?, ?, 'legacy', ?)""",
                (int(occurred_at), event_name[:80], session_key(raw_session), workspace[:1024], digest),
            )
            if cursor.rowcount:
                imported += 1
            else:
                skipped += 1
    print(f"Imported {imported} event(s); skipped {skipped} duplicate(s); ignored {invalid} invalid line(s).")


def intervals(times):
    """Turn sorted timestamps into worked intervals."""
    out, gap, tail = [], GAP_MIN * 60, TAIL_MIN * 60
    if not times:
        return out
    s = prev = times[0]
    for t in times[1:]:
        if t - prev > gap:
            out.append((s, prev + tail)); s = t
        prev = t
    out.append((s, prev + tail))
    return out


def union(iv):
    iv = sorted(iv); out = []
    for a, b in iv:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def total(iv):
    return sum(b - a for a, b in iv)


def rnd(h):
    return round(h / ROUND_H) * ROUND_H


def cmd_summary(args):
    day = next((a for a in args if a[:1].isdigit()), datetime.now().strftime("%Y-%m-%d"))
    m = load_map()
    ev = read_events(day)
    by_session, sess_client, unmapped = {}, {}, {}
    for t, _e, sid, path in ev:
        c = client_for(path, m)
        key = (sid, c or f"UNMAPPED:{path}")
        by_session.setdefault(key, []).append(t)
        if not c:
            unmapped[path] = unmapped.get(path, 0) + 1
    per_client = {}
    for (sid, c), ts in by_session.items():
        per_client.setdefault(c, []).extend(intervals(ts))
    rows, all_iv = [], []
    for c, iv in per_client.items():
        u = union(iv); all_iv += u
        rows.append({"client": c, "hours_raw": round(total(u) / 3600, 2), "hours": rnd(total(u) / 3600),
                     "first": datetime.fromtimestamp(u[0][0]).strftime("%H:%M"),
                     "last": datetime.fromtimestamp(u[-1][1]).strftime("%H:%M")})
    rows.sort(key=lambda r: -r["hours_raw"])
    wall = round(total(union(all_iv)) / 3600, 2)
    summed = round(sum(r["hours_raw"] for r in rows), 2)
    res = {"date": day, "rows": rows, "wall_hours": wall, "sum_hours": summed,
           "overlap_hours": round(summed - wall, 2), "unmapped_paths": sorted(unmapped),
           "events": len(ev), "gap_min": GAP_MIN, "tail_min": TAIL_MIN}
    if "--json" in args:
        print(json.dumps(res, indent=1)); return
    print(f"Claude-active time {day}  ({len(ev)} events, gap {GAP_MIN}m, tail {TAIL_MIN}m)")
    for r in rows:
        print(f"  {r['client']:<40} {r['hours']:>5.2f}h  (raw {r['hours_raw']:.2f}h, {r['first']}-{r['last']})")
    print(f"  wall-clock active: {wall:.2f}h" + (f"  · overlap between clients: {res['overlap_hours']:.2f}h" if res['overlap_hours'] > 0.05 else ""))
    if unmapped:
        print("  unmapped paths (run `ts.py map <prefix> <Client>`):")
        for p in sorted(unmapped):
            print(f"    {p}")


def cmd_map(args):
    if len(args) < 2:
        sys.exit("usage: ts.py map <path-prefix> <Client Name> [--client-id GUID]")
    client_id = arg_value(args, "--client-id")
    name_args = args[1:args.index("--client-id")] if "--client-id" in args else args[1:]
    p, c = os.path.abspath(os.path.expanduser(args[0])).rstrip("/"), " ".join(name_args)
    lines = open(MAP, encoding="utf-8").read().splitlines() if os.path.exists(MAP) else []
    lines = [l for l in lines if not l.startswith(p + "\t")] + [f"{p}\t{c}"]
    open(MAP, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    if STORAGE == "sqlite":
        with open_db() as db:
            review_store.map_workspace_client(db, p, c, client_id)
    print(f"mapped {p} -> {c}")


def cmd_clients(args):
    if STORAGE != "sqlite":
        sys.exit("clients requires TS_STORAGE=sqlite")
    import_path = arg_value(args, "--import")
    with open_db() as db:
        imported = None
        if import_path:
            if import_path == "-":
                content = sys.stdin.read()
            else:
                with open(os.path.abspath(os.path.expanduser(import_path)), encoding="utf-8") as stream:
                    content = stream.read()
            value = json.loads(content)
            clients = (value.get("clients") or value.get("Clients") or []) if isinstance(value, dict) else value
            if not isinstance(clients, list):
                sys.exit("client import must be a JSON array or an object containing a clients array")
            imported = review_store.import_clients(db, clients, "--replace" in args)
        values = review_store.list_clients(db)
    result = {"imported": imported, "clients": values}
    if "--json" in args or import_path:
        print(json.dumps(result, indent=2))
        return
    for client in values:
        identifier = client["id"] or "GUID NOT SYNCED"
        state = "active" if client["isActive"] else "inactive"
        print(f"{client['name']}\t{identifier}\t{state}")


def cmd_review_status(args):
    if STORAGE != "sqlite":
        sys.exit("review-status requires TS_STORAGE=sqlite")
    day = None if "--all" in args else next(
        (a for a in args if a[:1].isdigit()), datetime.now().strftime("%Y-%m-%d")
    )
    with open_db() as db:
        if day:
            review_store.sync_review_activities(db, day, load_map(), GAP_MIN, TAIL_MIN)
        else:
            review_store.sync_all_review_activities(db, load_map(), GAP_MIN, TAIL_MIN)
        result = review_store.review_summary(db, day)
    if "--json" in args:
        print(json.dumps(result, indent=2))
        return
    print(f"Local activity review {day or 'all dates'}")
    for status, count in sorted(result["counts"].items()):
        print(f"  {status:<10} {count}")
    print(f"  active time {result['activeMinutes']}m")


def cmd_preview_upload(args):
    if STORAGE != "sqlite":
        sys.exit("preview-upload requires TS_STORAGE=sqlite")
    day = next((a for a in args if a[:1].isdigit()), None)
    if day:
        review_entries(day)
    with open_db() as db:
        values = review_store.preview_payloads(db, day)
    result = {"date": day, "activities": values}
    print(json.dumps(result, indent=2))


def cmd_prepare_upload(args):
    if STORAGE != "sqlite":
        sys.exit("prepare-upload requires TS_STORAGE=sqlite")
    day = next((a for a in args if a[:1].isdigit()), None)
    if day:
        review_entries(day)
    with open_db() as db:
        previews = review_store.preview_payloads(db, day)
    if not previews:
        sys.exit("no approved activities are available to prepare")
    print(json.dumps({"date": day, "activities": previews}, indent=2))
    if "--yes" not in args:
        if not sys.stdin.isatty():
            sys.exit("re-run with --yes after reviewing this exact payload")
        answer = input("Prepare these immutable payloads for upload? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("No payloads were prepared.")
            return
    try:
        with open_db() as db:
            prepared = review_store.prepare_submissions(
                db, day, arg_value(args, "--target", "tdc-internal-api")
            )
    except ValueError as error:
        sys.exit(str(error))
    print(json.dumps({"prepared": prepared}, indent=2))


def cmd_submissions(args):
    if STORAGE != "sqlite":
        sys.exit("submissions requires TS_STORAGE=sqlite")
    submission_id = arg_value(args, "--id")
    status = arg_value(args, "--status")
    try:
        with open_db() as db:
            if submission_id and status:
                value = review_store.update_submission(
                    db,
                    int(submission_id),
                    status,
                    int(arg_value(args, "--http-status")) if arg_value(args, "--http-status") else None,
                    arg_value(args, "--receipt-id"),
                    arg_value(args, "--error-code"),
                    arg_value(args, "--error-message"),
                )
                result = {"submission": value}
            else:
                result = {"submissions": review_store.list_submissions(db, status)}
    except (KeyError, ValueError) as error:
        sys.exit(str(error))
    print(json.dumps(result, indent=2))


def cmd_review_ui(args):
    if STORAGE != "sqlite":
        sys.exit("review-ui requires TS_STORAGE=sqlite")
    day = next((a for a in args if a[:1].isdigit()), datetime.now().strftime("%Y-%m-%d"))
    port_value = arg_value(args, "--port", "0")
    try:
        port = int(port_value)
    except ValueError:
        sys.exit("--port must be a number")
    import review_ui
    review_ui.run_server(
        open_db,
        load_map,
        day,
        GAP_MIN,
        TAIL_MIN,
        port=port,
        open_browser="--no-open" not in args,
    )


if __name__ == "__main__":
    cmd, rest = (sys.argv[1] if len(sys.argv) > 1 else "summary"), sys.argv[2:]
    {"hook": lambda: cmd_hook(rest), "summary": lambda: cmd_summary(rest), "map": lambda: cmd_map(rest),
     "review": lambda: cmd_review(rest), "import-tsv": lambda: cmd_import_tsv(rest),
     "export": lambda: cmd_export(rest), "clients": lambda: cmd_clients(rest),
     "review-status": lambda: cmd_review_status(rest),
     "preview-upload": lambda: cmd_preview_upload(rest),
     "prepare-upload": lambda: cmd_prepare_upload(rest),
     "submissions": lambda: cmd_submissions(rest),
     "review-ui": lambda: cmd_review_ui(rest)}.get(
        cmd, lambda: sys.exit(__doc__))()
