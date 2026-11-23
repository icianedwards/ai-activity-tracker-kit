"""Local review, segmentation, payload, and submission state for ICE Context Kit activity."""

import hashlib
import json
import os
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime


PAYLOAD_VERSION = "1.0"
REVIEW_STATUSES = {
    "pending", "approved", "uploading", "uploaded", "failed", "private", "excluded", "merged"
}
SUBMISSION_STATUSES = {"prepared", "uploading", "uploaded", "failed"}
OPTIONAL_FIELDS = {
    "source": True,
    "project": True,
    "summary": True,
    "eventCounts": False,
    "editedFiles": False,
}


def _now():
    return int(time.time())


def _json(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _loads(value, fallback):
    try:
        loaded = json.loads(value or "")
        return loaded if isinstance(loaded, type(fallback)) else fallback
    except (TypeError, ValueError):
        return fallback


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _workspace_key(path):
    return "sha256:" + _digest(os.path.normpath(path))


def _activity_key(seed):
    return "ctx-" + _digest(seed)[:24]


def ensure_schema(db):
    """Create the additive review schema and migrate safe legacy metadata."""
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS client_catalog (
            catalog_key TEXT PRIMARY KEY,
            client_id TEXT,
            client_name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            synced_at INTEGER NOT NULL,
            UNIQUE(client_id),
            UNIQUE(client_name COLLATE NOCASE)
        );

        CREATE TABLE IF NOT EXISTS workspace_client_mappings (
            workspace_prefix TEXT PRIMARY KEY,
            catalog_key TEXT,
            client_id TEXT,
            client_name TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            FOREIGN KEY(catalog_key) REFERENCES client_catalog(catalog_key)
        );

        CREATE TABLE IF NOT EXISTS activity_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            segment_key TEXT NOT NULL UNIQUE,
            work_date TEXT NOT NULL,
            workspace_path TEXT NOT NULL,
            workspace_key TEXT NOT NULL,
            started_at INTEGER NOT NULL,
            ended_at INTEGER NOT NULL,
            duration_seconds INTEGER NOT NULL,
            source TEXT NOT NULL,
            event_count INTEGER NOT NULL,
            event_ids_json TEXT NOT NULL,
            action_counts_json TEXT NOT NULL,
            edited_files_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_activity_segments_day_workspace
            ON activity_segments(work_date, workspace_key, started_at);

        CREATE TABLE IF NOT EXISTS review_activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            activity_key TEXT NOT NULL UNIQUE,
            work_date TEXT NOT NULL,
            workspace_path TEXT NOT NULL,
            workspace_key TEXT NOT NULL,
            client_id TEXT,
            client_name TEXT,
            project_name TEXT NOT NULL,
            summary TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            origin TEXT NOT NULL DEFAULT 'generated',
            is_manual INTEGER NOT NULL DEFAULT 0,
            inclusion_json TEXT NOT NULL,
            payload_version TEXT NOT NULL DEFAULT '1.0',
            payload_hash TEXT,
            approved_at INTEGER,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_review_activities_day_status
            ON review_activities(work_date, status, updated_at);

        CREATE TABLE IF NOT EXISTS review_activity_segments (
            review_activity_id INTEGER NOT NULL,
            segment_id INTEGER NOT NULL,
            PRIMARY KEY(review_activity_id, segment_id),
            FOREIGN KEY(review_activity_id) REFERENCES review_activities(id) ON DELETE CASCADE,
            FOREIGN KEY(segment_id) REFERENCES activity_segments(id)
        );

        CREATE TABLE IF NOT EXISTS review_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_activity_id INTEGER NOT NULL,
            changed_at INTEGER NOT NULL,
            changed_by TEXT NOT NULL,
            before_json TEXT NOT NULL,
            after_json TEXT NOT NULL,
            FOREIGN KEY(review_activity_id) REFERENCES review_activities(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS submission_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_activity_id INTEGER NOT NULL,
            target TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload_version TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            prepared_at INTEGER NOT NULL,
            attempted_at INTEGER,
            completed_at INTEGER,
            http_status INTEGER,
            receipt_id TEXT,
            error_code TEXT,
            error_message TEXT,
            FOREIGN KEY(review_activity_id) REFERENCES review_activities(id)
        );
        CREATE INDEX IF NOT EXISTS ix_submission_attempts_status
            ON submission_attempts(status, prepared_at);
        """
    )
    _migrate_legacy_clients(db)
    _migrate_legacy_reviews(db)


def _catalog_key(client_id, client_name):
    if client_id:
        return "id:" + str(client_id).lower()
    return "name:" + _digest(client_name.strip().lower())[:24]


def upsert_client(db, client_name, client_id=None, is_active=True, synced_at=None):
    name = str(client_name or "").strip()[:200]
    identifier = str(client_id or "").strip() or None
    if not name:
        raise ValueError("client name is required")
    key = _catalog_key(identifier, name)
    stamp = synced_at or _now()
    existing = db.execute(
        "SELECT catalog_key FROM client_catalog WHERE client_name = ? COLLATE NOCASE",
        (name,),
    ).fetchone()
    if existing:
        key = existing["catalog_key"]
    db.execute(
        """INSERT INTO client_catalog
           (catalog_key, client_id, client_name, is_active, synced_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(catalog_key) DO UPDATE SET
             client_id = excluded.client_id,
             client_name = excluded.client_name,
             is_active = excluded.is_active,
             synced_at = excluded.synced_at""",
        (key, identifier, name, 1 if is_active else 0, stamp),
    )
    if identifier:
        db.execute(
            """UPDATE workspace_client_mappings
               SET catalog_key = ?, client_id = ?, client_name = ?, updated_at = ?
               WHERE client_name = ? COLLATE NOCASE""",
            (key, identifier, name, stamp, name),
        )
        db.execute(
            """UPDATE review_activities
               SET client_id = ?, client_name = ?, updated_at = ?
               WHERE client_name = ? COLLATE NOCASE AND client_id IS NULL""",
            (identifier, name, stamp, name),
        )
    return key


def _migrate_legacy_clients(db):
    now = _now()
    for row in db.execute(
        "SELECT DISTINCT client FROM work_entries WHERE client IS NOT NULL AND trim(client) <> ''"
    ).fetchall():
        upsert_client(db, row[0], synced_at=now)
    legacy_mappings = db.execute(
        """SELECT workspace_path, client FROM work_entries
           WHERE client IS NOT NULL AND trim(client) <> ''
           ORDER BY length(workspace_path)"""
    ).fetchall()
    for row in legacy_mappings:
        map_workspace_client(db, row["workspace_path"], row["client"])


def _migrate_legacy_reviews(db):
    if db.execute("SELECT 1 FROM review_activities LIMIT 1").fetchone():
        return
    legacy = db.execute(
        """SELECT * FROM work_entries
           WHERE status <> 'pending' OR client_is_manual = 1 OR summary_is_manual = 1
           ORDER BY id"""
    ).fetchall()
    now = _now()
    for row in legacy:
        workspace = row["workspace_path"]
        segment_key = "legacy:" + _digest(f"{row['id']}|{row['started_at']}|{row['ended_at']}")
        db.execute(
            """INSERT OR IGNORE INTO activity_segments
               (segment_key, work_date, workspace_path, workspace_key, started_at, ended_at,
                duration_seconds, source, event_count, event_ids_json, action_counts_json,
                edited_files_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'unknown', 0, '[]', '{}', '[]', ?, ?)""",
            (segment_key, row["work_date"], workspace, _workspace_key(workspace), row["started_at"],
             row["ended_at"], row["duration_seconds"], now, now),
        )
        segment = db.execute(
            "SELECT id FROM activity_segments WHERE segment_key = ?", (segment_key,)
        ).fetchone()
        client_id = None
        if row["client"]:
            catalog = db.execute(
                "SELECT client_id FROM client_catalog WHERE client_name = ? COLLATE NOCASE",
                (row["client"],),
            ).fetchone()
            client_id = catalog[0] if catalog else None
        activity_key = _activity_key(f"legacy|{row['id']}|{workspace}|{row['work_date']}")
        cursor = db.execute(
            """INSERT INTO review_activities
               (activity_key, work_date, workspace_path, workspace_key, client_id, client_name,
                project_name, summary, status, origin, is_manual, inclusion_json,
                payload_version, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'legacy', 1, ?, ?, ?, ?)""",
            (activity_key, row["work_date"], workspace, _workspace_key(workspace), client_id,
             row["client"], os.path.basename(workspace.rstrip(os.sep)) or "Project", row["summary"],
             row["status"], _json(OPTIONAL_FIELDS), PAYLOAD_VERSION, now, now),
        )
        db.execute(
            "INSERT INTO review_activity_segments(review_activity_id, segment_id) VALUES (?, ?)",
            (cursor.lastrowid, segment["id"]),
        )


def import_clients(db, clients, replace=False):
    now = _now()
    if replace:
        db.execute("UPDATE client_catalog SET is_active = 0, synced_at = ?", (now,))
    imported = 0
    for value in clients:
        if not isinstance(value, dict):
            continue
        name = value.get("name") or value.get("clientName") or value.get("ClientName")
        identifier = (
            value.get("id") or value.get("clientId") or value.get("costClientId")
            or value.get("Id") or value.get("ClientId") or value.get("CostClientId")
        )
        if not name:
            continue
        is_active = value.get("isActive", value.get("IsActive", True))
        upsert_client(db, name, identifier, is_active, now)
        imported += 1
    return imported


def list_clients(db):
    rows = db.execute(
        """SELECT catalog_key, client_id, client_name, is_active
           FROM client_catalog ORDER BY is_active DESC, client_name COLLATE NOCASE"""
    ).fetchall()
    return [{
        "catalogKey": row["catalog_key"],
        "id": row["client_id"],
        "name": row["client_name"],
        "isActive": bool(row["is_active"]),
    } for row in rows]


def map_workspace_client(db, workspace_prefix, client_name, client_id=None):
    prefix = os.path.abspath(os.path.expanduser(workspace_prefix)).rstrip(os.sep)
    key = upsert_client(db, client_name, client_id)
    db.execute(
        """INSERT INTO workspace_client_mappings
           (workspace_prefix, catalog_key, client_id, client_name, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(workspace_prefix) DO UPDATE SET
             catalog_key = excluded.catalog_key,
             client_id = excluded.client_id,
             client_name = excluded.client_name,
             updated_at = excluded.updated_at""",
        (prefix, key, client_id or None, client_name.strip(), _now()),
    )


def _mapped_client(db, workspace, legacy_mapping):
    mappings = db.execute(
        """SELECT workspace_prefix, client_id, client_name
           FROM workspace_client_mappings ORDER BY length(workspace_prefix) DESC"""
    ).fetchall()
    for mapping in mappings:
        prefix = mapping["workspace_prefix"]
        if workspace == prefix or workspace.startswith(prefix + os.sep):
            return mapping["client_id"], mapping["client_name"], prefix
    for prefix, name in legacy_mapping:
        if workspace == prefix or workspace.startswith(prefix + os.sep):
            catalog = db.execute(
                "SELECT client_id FROM client_catalog WHERE client_name = ? COLLATE NOCASE",
                (name,),
            ).fetchone()
            return (catalog[0] if catalog else None), name, prefix
    return None, None, workspace


def _event_groups(events, gap_seconds, tail_seconds):
    if not events:
        return []
    groups = []
    current = [events[0]]
    for event in events[1:]:
        if event["occurred_at"] - current[-1]["occurred_at"] > gap_seconds:
            groups.append(current)
            current = []
        current.append(event)
    groups.append(current)
    return [(group, group[0]["occurred_at"], group[-1]["occurred_at"] + tail_seconds)
            for group in groups]


def _safe_summary(workspace, events):
    files = []
    for event in events:
        for path in _loads(event["edited_files_json"], []):
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


def sync_review_activities(db, day, legacy_mapping, gap_minutes=15, tail_minutes=5):
    start = int(datetime.strptime(day, "%Y-%m-%d").timestamp())
    end = start + 86400
    all_events = db.execute(
        """SELECT id, occurred_at, workspace_path, source, tool_name, edited_files_json
           FROM activity_events WHERE occurred_at >= ? AND occurred_at < ?
           ORDER BY workspace_path, occurred_at, id""",
        (start, end),
    ).fetchall()
    protected_ids = set()
    protected = db.execute(
        """SELECT s.event_ids_json
           FROM activity_segments s
           JOIN review_activity_segments rs ON rs.segment_id = s.id
           JOIN review_activities r ON r.id = rs.review_activity_id
           WHERE r.work_date = ? AND (r.status <> 'pending' OR r.is_manual = 1)""",
        (day,),
    ).fetchall()
    for row in protected:
        protected_ids.update(_loads(row[0], []))
    generated = db.execute(
        """SELECT id FROM review_activities
           WHERE work_date = ? AND status = 'pending' AND origin = 'generated' AND is_manual = 0""",
        (day,),
    ).fetchall()
    generated_ids = {row["id"] for row in generated}
    seen_generated_ids = set()
    grouped = defaultdict(list)
    for event in all_events:
        if event["id"] not in protected_ids:
            client_id, client_name, group_workspace = _mapped_client(
                db, event["workspace_path"], legacy_mapping
            )
            grouped[(group_workspace, client_id, client_name)].append(event)
    now = _now()
    created = 0
    for (workspace, client_id, client_name), events in grouped.items():
        segment_ids = []
        segment_keys = []
        for group, segment_start, segment_end in _event_groups(
            events, gap_minutes * 60, tail_minutes * 60
        ):
            event_ids = [event["id"] for event in group]
            key = "seg-" + _digest(f"{workspace}|{event_ids[0]}|{event_ids[-1]}")[:24]
            sources = sorted({event["source"] or "unknown" for event in group})
            source = sources[0] if len(sources) == 1 else "mixed"
            actions = Counter(event["tool_name"] or "lifecycle" for event in group)
            files = []
            for event in group:
                for path in _loads(event["edited_files_json"], []):
                    if path not in files:
                        files.append(path)
            db.execute(
                """INSERT INTO activity_segments
                   (segment_key, work_date, workspace_path, workspace_key, started_at, ended_at,
                    duration_seconds, source, event_count, event_ids_json, action_counts_json,
                    edited_files_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(segment_key) DO UPDATE SET
                     ended_at = excluded.ended_at,
                     duration_seconds = excluded.duration_seconds,
                     source = excluded.source,
                     event_count = excluded.event_count,
                     event_ids_json = excluded.event_ids_json,
                     action_counts_json = excluded.action_counts_json,
                     edited_files_json = excluded.edited_files_json,
                     updated_at = excluded.updated_at""",
                (key, day, workspace, _workspace_key(workspace), segment_start, segment_end,
                 segment_end - segment_start, source, len(group), _json(event_ids), _json(dict(actions)),
                 _json(files), now, now),
            )
            segment = db.execute(
                "SELECT id FROM activity_segments WHERE segment_key = ?", (key,)
            ).fetchone()
            segment_ids.append(segment["id"])
            segment_keys.append(key)
        if not segment_ids:
            continue
        activity_key = _activity_key(f"generated|{day}|{workspace}|{'|'.join(segment_keys)}")
        db.execute(
            """INSERT INTO review_activities
               (activity_key, work_date, workspace_path, workspace_key, client_id, client_name,
                project_name, summary, status, origin, is_manual, inclusion_json,
                payload_version, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'generated', 0, ?, ?, ?, ?)
               ON CONFLICT(activity_key) DO UPDATE SET
                 client_id = excluded.client_id,
                 client_name = excluded.client_name,
                 project_name = excluded.project_name,
                 summary = excluded.summary,
                 updated_at = excluded.updated_at""",
            (activity_key, day, workspace, _workspace_key(workspace), client_id, client_name,
             os.path.basename(workspace.rstrip(os.sep)) or "Project", _safe_summary(workspace, events),
             _json(OPTIONAL_FIELDS), PAYLOAD_VERSION, now, now),
        )
        activity = db.execute(
            "SELECT id FROM review_activities WHERE activity_key = ?", (activity_key,)
        ).fetchone()
        activity_id = activity["id"]
        seen_generated_ids.add(activity_id)
        db.execute(
            "DELETE FROM review_activity_segments WHERE review_activity_id = ?", (activity_id,)
        )
        for segment_id in segment_ids:
            db.execute(
                "INSERT INTO review_activity_segments(review_activity_id, segment_id) VALUES (?, ?)",
                (activity_id, segment_id),
            )
        created += 1
    for activity_id in generated_ids - seen_generated_ids:
        db.execute("DELETE FROM review_activities WHERE id = ?", (activity_id,))
    return created


def sync_all_review_activities(db, legacy_mapping, gap_minutes=15, tail_minutes=5):
    days = sorted({
        datetime.fromtimestamp(row[0]).strftime("%Y-%m-%d")
        for row in db.execute("SELECT occurred_at FROM activity_events").fetchall()
    })
    for day in days:
        sync_review_activities(db, day, legacy_mapping, gap_minutes, tail_minutes)
    return days


def _row_snapshot(row):
    return {
        "clientId": row["client_id"],
        "clientName": row["client_name"],
        "projectName": row["project_name"],
        "summary": row["summary"],
        "status": row["status"],
        "inclusion": _loads(row["inclusion_json"], dict(OPTIONAL_FIELDS)),
    }


def _record_revision(db, activity_id, before, after, changed_by):
    if before == after:
        return
    db.execute(
        """INSERT INTO review_revisions
           (review_activity_id, changed_at, changed_by, before_json, after_json)
           VALUES (?, ?, ?, ?, ?)""",
        (activity_id, _now(), changed_by[:80], _json(before), _json(after)),
    )


def _activity_segments(db, activity_id):
    return db.execute(
        """SELECT s.* FROM activity_segments s
           JOIN review_activity_segments rs ON rs.segment_id = s.id
           WHERE rs.review_activity_id = ? ORDER BY s.started_at, s.id""",
        (activity_id,),
    ).fetchall()


def _union_intervals(segments):
    result = []
    for segment in sorted(segments, key=lambda item: item["started_at"]):
        start, end = segment["started_at"], segment["ended_at"]
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def _activity_view(db, row):
    segments = _activity_segments(db, row["id"])
    intervals = _union_intervals(segments)
    duration = sum(end - start for start, end in intervals)
    sources = sorted({segment["source"] for segment in segments})
    source = sources[0] if len(sources) == 1 else "mixed"
    counts = Counter()
    files = []
    event_count = 0
    segment_values = []
    for segment in segments:
        counts.update(_loads(segment["action_counts_json"], {}))
        event_count += segment["event_count"]
        for path in _loads(segment["edited_files_json"], []):
            if path not in files:
                files.append(path)
        segment_values.append({
            "id": segment["id"],
            "startedAt": segment["started_at"],
            "endedAt": segment["ended_at"],
            "first": datetime.fromtimestamp(segment["started_at"]).strftime("%H:%M"),
            "last": datetime.fromtimestamp(segment["ended_at"]).strftime("%H:%M"),
            "minutes": max(1, round(segment["duration_seconds"] / 60)),
            "source": segment["source"],
            "eventCount": segment["event_count"],
        })
    inclusion = dict(OPTIONAL_FIELDS)
    inclusion.update(_loads(row["inclusion_json"], {}))
    submission = None
    if row["payload_hash"]:
        submission = db.execute(
            """SELECT status FROM submission_attempts
               WHERE review_activity_id = ? AND payload_hash = ?
               ORDER BY CASE status WHEN 'uploaded' THEN 0 ELSE 1 END, id DESC
               LIMIT 1""",
            (row["id"], row["payload_hash"]),
        ).fetchone()
    lifecycle_status = submission["status"] if submission else row["status"]
    return {
        "id": row["id"],
        "activityKey": row["activity_key"],
        "date": row["work_date"],
        "workspace": row["workspace_path"],
        "workspaceKey": row["workspace_key"],
        "clientId": row["client_id"],
        "clientName": row["client_name"],
        "projectName": row["project_name"],
        "summary": row["summary"],
        "status": row["status"],
        "lifecycleStatus": lifecycle_status,
        "source": source,
        "firstObservedAt": intervals[0][0] if intervals else None,
        "lastObservedAt": intervals[-1][1] if intervals else None,
        "first": datetime.fromtimestamp(intervals[0][0]).strftime("%H:%M") if intervals else "",
        "last": datetime.fromtimestamp(intervals[-1][1]).strftime("%H:%M") if intervals else "",
        "activeMinutes": max(1, round(duration / 60)) if intervals else 0,
        "eventCount": event_count,
        "eventCounts": dict(counts),
        "editedFiles": files,
        "segments": segment_values,
        "inclusion": inclusion,
        "payloadVersion": row["payload_version"],
        "payloadHash": row["payload_hash"],
        "clientGuidMissing": not bool(row["client_id"]),
        "updatedAt": row["updated_at"],
    }


def list_activities(db, day=None, include_merged=False):
    clauses = []
    values = []
    if day:
        clauses.append("work_date = ?")
        values.append(day)
    if not include_merged:
        clauses.append("status <> 'merged'")
    query = "SELECT * FROM review_activities"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY work_date DESC, (SELECT min(s.started_at) FROM activity_segments s JOIN review_activity_segments rs ON rs.segment_id = s.id WHERE rs.review_activity_id = review_activities.id), id"
    return [_activity_view(db, row) for row in db.execute(query, values).fetchall()]


def _deletable_activity_ids(db, category):
    if category == "excluded":
        rows = db.execute(
            "SELECT id FROM review_activities WHERE status = 'excluded'"
        ).fetchall()
    elif category == "private":
        rows = db.execute(
            "SELECT id FROM review_activities WHERE status = 'private'"
        ).fetchall()
    elif category == "uploaded":
        rows = db.execute(
            """SELECT id FROM review_activities WHERE status = 'uploaded'
               UNION
               SELECT DISTINCT r.id
               FROM review_activities r
               JOIN submission_attempts s ON s.review_activity_id = r.id
               WHERE r.payload_hash IS NOT NULL
                 AND s.payload_hash = r.payload_hash
                 AND s.status = 'uploaded'"""
        ).fetchall()
    else:
        raise ValueError("category must be excluded, private, or uploaded")
    return [row["id"] for row in rows]


def deletion_counts(db):
    return {
        category: len(_deletable_activity_ids(db, category))
        for category in ("excluded", "private", "uploaded")
    }


def delete_activities(db, category):
    activity_ids = _deletable_activity_ids(db, category)
    if not activity_ids:
        return {"category": category, "deletedActivities": 0, "deletedEvents": 0}

    placeholders = ",".join("?" for _ in activity_ids)
    activities = db.execute(
        f"SELECT work_date, workspace_path FROM review_activities WHERE id IN ({placeholders})",
        activity_ids,
    ).fetchall()
    segment_rows = db.execute(
        f"""SELECT s.id, s.event_ids_json
            FROM activity_segments s
            JOIN review_activity_segments rs ON rs.segment_id = s.id
            WHERE rs.review_activity_id IN ({placeholders})""",
        activity_ids,
    ).fetchall()
    segment_ids = {row["id"] for row in segment_rows}
    candidate_event_ids = {
        int(event_id)
        for row in segment_rows
        for event_id in _loads(row["event_ids_json"], [])
    }

    db.execute(
        f"DELETE FROM submission_attempts WHERE review_activity_id IN ({placeholders})",
        activity_ids,
    )
    db.execute(
        f"DELETE FROM review_activity_segments WHERE review_activity_id IN ({placeholders})",
        activity_ids,
    )
    db.execute(
        f"DELETE FROM review_activities WHERE id IN ({placeholders})",
        activity_ids,
    )

    if segment_ids:
        segment_placeholders = ",".join("?" for _ in segment_ids)
        db.execute(
            f"""DELETE FROM activity_segments
                WHERE id IN ({segment_placeholders})
                  AND NOT EXISTS (
                    SELECT 1 FROM review_activity_segments rs
                    WHERE rs.segment_id = activity_segments.id
                  )""",
            list(segment_ids),
        )

    retained_event_ids = {
        int(event_id)
        for row in db.execute("SELECT event_ids_json FROM activity_segments").fetchall()
        for event_id in _loads(row["event_ids_json"], [])
    }
    removable_event_ids = candidate_event_ids - retained_event_ids
    if removable_event_ids:
        event_placeholders = ",".join("?" for _ in removable_event_ids)
        db.execute(
            f"DELETE FROM activity_events WHERE id IN ({event_placeholders})",
            list(removable_event_ids),
        )

    for activity in activities:
        remaining = db.execute(
            """SELECT 1 FROM review_activities
               WHERE work_date = ? AND workspace_path = ? LIMIT 1""",
            (activity["work_date"], activity["workspace_path"]),
        ).fetchone()
        if remaining:
            continue
        legacy_rows = db.execute(
            "SELECT id FROM work_entries WHERE work_date = ? AND workspace_path = ?",
            (activity["work_date"], activity["workspace_path"]),
        ).fetchall()
        legacy_ids = [row["id"] for row in legacy_rows]
        if legacy_ids:
            legacy_placeholders = ",".join("?" for _ in legacy_ids)
            db.execute(
                f"DELETE FROM submissions WHERE work_entry_id IN ({legacy_placeholders})",
                legacy_ids,
            )
            db.execute(
                f"DELETE FROM work_entries WHERE id IN ({legacy_placeholders})",
                legacy_ids,
            )

    return {
        "category": category,
        "deletedActivities": len(activity_ids),
        "deletedEvents": len(removable_event_ids),
    }


def update_activity(db, activity_id, changes, changed_by="local-review-ui"):
    row = db.execute("SELECT * FROM review_activities WHERE id = ?", (activity_id,)).fetchone()
    if not row:
        raise KeyError(f"review activity #{activity_id} was not found")
    before = _row_snapshot(row)
    client_id = changes.get("clientId", row["client_id"])
    client_name = changes.get("clientName", row["client_name"])
    project = str(changes.get("projectName", row["project_name"]) or "").strip()[:200]
    summary = str(changes.get("summary", row["summary"]) or "").strip()[:2000]
    inclusion = dict(OPTIONAL_FIELDS)
    inclusion.update(_loads(row["inclusion_json"], {}))
    supplied_inclusion = changes.get("inclusion")
    if isinstance(supplied_inclusion, dict):
        for key in OPTIONAL_FIELDS:
            if key in supplied_inclusion:
                inclusion[key] = bool(supplied_inclusion[key])
    content_changed = any([
        client_id != row["client_id"], client_name != row["client_name"],
        project != row["project_name"], summary != row["summary"],
        inclusion != _loads(row["inclusion_json"], {}),
    ])
    status = row["status"]
    if content_changed and status in {"approved", "uploading", "uploaded", "failed"}:
        status = "pending"
    db.execute(
        """UPDATE review_activities SET
             client_id = ?, client_name = ?, project_name = ?, summary = ?, status = ?,
             is_manual = 1, inclusion_json = ?, payload_hash = NULL,
             approved_at = CASE WHEN ? = 'approved' THEN approved_at ELSE NULL END,
             updated_at = ?
           WHERE id = ?""",
        (client_id or None, client_name or None, project, summary, status, _json(inclusion), status,
         _now(), activity_id),
    )
    if client_name:
        map_workspace_client(db, row["workspace_path"], client_name, client_id)
    updated = db.execute("SELECT * FROM review_activities WHERE id = ?", (activity_id,)).fetchone()
    _record_revision(db, activity_id, before, _row_snapshot(updated), changed_by)
    return _activity_view(db, updated)


def set_activity_status(db, activity_id, status, changed_by="local-review-ui"):
    if status not in {"pending", "approved", "private", "excluded"}:
        raise ValueError("status must be pending, approved, private, or excluded")
    row = db.execute("SELECT * FROM review_activities WHERE id = ?", (activity_id,)).fetchone()
    if not row:
        raise KeyError(f"review activity #{activity_id} was not found")
    before = _row_snapshot(row)
    approved_at = None
    payload_hash = None
    if status == "approved":
        if not row["client_name"]:
            raise ValueError("select a client before approving this activity")
        if not str(row["summary"] or "").strip():
            raise ValueError("enter a summary before approving this activity")
        approved_at = _now()
        view = _activity_view(db, row)
        payload_hash = _digest(_json(build_payload(view)))
    db.execute(
        """UPDATE review_activities SET status = ?, is_manual = 1, approved_at = ?,
             payload_hash = ?, updated_at = ? WHERE id = ?""",
        (status, approved_at, payload_hash, _now(), activity_id),
    )
    updated = db.execute("SELECT * FROM review_activities WHERE id = ?", (activity_id,)).fetchone()
    _record_revision(db, activity_id, before, _row_snapshot(updated), changed_by)
    return _activity_view(db, updated)


def split_activity(db, activity_id, after_segment_id, changed_by="local-review-ui"):
    row = db.execute("SELECT * FROM review_activities WHERE id = ?", (activity_id,)).fetchone()
    if not row:
        raise KeyError(f"review activity #{activity_id} was not found")
    segments = _activity_segments(db, activity_id)
    ids = [segment["id"] for segment in segments]
    if after_segment_id not in ids or ids.index(after_segment_id) == len(ids) - 1:
        raise ValueError("choose a segment that has another segment after it")
    moving = ids[ids.index(after_segment_id) + 1:]
    now = _now()
    new_key = _activity_key(f"split|{row['activity_key']}|{after_segment_id}|{now}")
    cursor = db.execute(
        """INSERT INTO review_activities
           (activity_key, work_date, workspace_path, workspace_key, client_id, client_name,
            project_name, summary, status, origin, is_manual, inclusion_json,
            payload_version, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'split', 1, ?, ?, ?, ?)""",
        (new_key, row["work_date"], row["workspace_path"], row["workspace_key"], row["client_id"],
         row["client_name"], row["project_name"], row["summary"], row["inclusion_json"],
         row["payload_version"], now, now),
    )
    new_id = cursor.lastrowid
    for segment_id in moving:
        db.execute(
            "UPDATE review_activity_segments SET review_activity_id = ? WHERE review_activity_id = ? AND segment_id = ?",
            (new_id, activity_id, segment_id),
        )
    db.execute(
        """UPDATE review_activities SET status = 'pending', is_manual = 1,
             payload_hash = NULL, approved_at = NULL, updated_at = ? WHERE id = ?""",
        (now, activity_id),
    )
    _record_revision(db, activity_id, _row_snapshot(row),
                     _row_snapshot(db.execute("SELECT * FROM review_activities WHERE id = ?", (activity_id,)).fetchone()),
                     changed_by)
    return [_activity_view(db, db.execute("SELECT * FROM review_activities WHERE id = ?", (value,)).fetchone())
            for value in (activity_id, new_id)]


def merge_activities(db, activity_ids, changed_by="local-review-ui"):
    ids = list(dict.fromkeys(int(value) for value in activity_ids))
    if len(ids) < 2:
        raise ValueError("select at least two activities to merge")
    rows = [db.execute("SELECT * FROM review_activities WHERE id = ?", (value,)).fetchone() for value in ids]
    if any(row is None for row in rows):
        raise KeyError("one or more review activities were not found")
    if len({row["work_date"] for row in rows}) != 1:
        raise ValueError("only activities from the same date can be merged")
    target = rows[0]
    now = _now()
    paths = [row["workspace_path"] for row in rows]
    common_workspace = os.path.commonpath(paths)
    if common_workspace in {os.path.sep, os.path.expanduser("~")}:
        common_workspace = target["workspace_path"]
    client_names = {row["client_name"] for row in rows if row["client_name"]}
    client_ids = {row["client_id"] for row in rows if row["client_id"]}
    merged_client_name = next(iter(client_names)) if len(client_names) == 1 else target["client_name"]
    merged_client_id = next(iter(client_ids)) if len(client_ids) == 1 else target["client_id"]
    for row in rows[1:]:
        db.execute(
            """INSERT OR IGNORE INTO review_activity_segments(review_activity_id, segment_id)
               SELECT ?, segment_id FROM review_activity_segments WHERE review_activity_id = ?""",
            (target["id"], row["id"]),
        )
        db.execute("DELETE FROM review_activity_segments WHERE review_activity_id = ?", (row["id"],))
        db.execute(
            "UPDATE review_activities SET status = 'merged', is_manual = 1, updated_at = ? WHERE id = ?",
            (now, row["id"]),
        )
    db.execute(
        """UPDATE review_activities SET status = 'pending', origin = 'merged', is_manual = 1,
             workspace_path = ?, workspace_key = ?, client_id = ?, client_name = ?,
             project_name = ?, payload_hash = NULL, approved_at = NULL, updated_at = ?
           WHERE id = ?""",
        (common_workspace, _workspace_key(common_workspace), merged_client_id, merged_client_name,
         os.path.basename(common_workspace.rstrip(os.sep)) or target["project_name"], now, target["id"]),
    )
    if merged_client_name:
        map_workspace_client(db, common_workspace, merged_client_name, merged_client_id)
    updated = db.execute("SELECT * FROM review_activities WHERE id = ?", (target["id"],)).fetchone()
    _record_revision(db, target["id"], _row_snapshot(target), _row_snapshot(updated), changed_by)
    return _activity_view(db, updated)


def build_payload(activity):
    inclusion = activity["inclusion"]
    payload = {
        "schemaVersion": activity["payloadVersion"],
        "clientActivityId": activity["activityKey"],
        "activityDate": activity["date"],
        "client": {
            "id": activity["clientId"],
            "name": activity["clientName"],
        },
        "workspaceKey": activity["workspaceKey"],
        "time": {
            "firstObservedAt": activity["firstObservedAt"],
            "lastObservedAt": activity["lastObservedAt"],
            "activeMinutes": activity["activeMinutes"],
            "calculation": {
                "method": "activity-segments-v1",
                "segments": len(activity["segments"]),
            },
        },
        "privacy": {
            "locallyReviewed": True,
            "includesPrompts": False,
            "includesCommandText": False,
            "includesFileContents": False,
            "includesToolOutput": False,
            "includesAbsolutePaths": False,
            "includesRawSessionIdentifiers": False,
        },
    }
    if inclusion.get("source"):
        payload["source"] = {"collector": "ai-activity-tracker-kit", "agent": activity["source"]}
    if inclusion.get("project"):
        payload["project"] = {"name": activity["projectName"]}
    if inclusion.get("summary"):
        payload["summary"] = activity["summary"]
    if inclusion.get("eventCounts"):
        payload.setdefault("evidence", {})["eventCounts"] = activity["eventCounts"]
    if inclusion.get("editedFiles"):
        payload.setdefault("evidence", {})["editedFiles"] = activity["editedFiles"]
    return payload


def preview_payloads(db, day=None, include_retryable=False):
    activities = [
        activity for activity in list_activities(db, day)
        if activity["lifecycleStatus"] == "approved"
        or (include_retryable
            and activity["lifecycleStatus"] in {"prepared", "uploading", "failed"})
    ]
    values = []
    for activity in activities:
        payload = build_payload(activity)
        values.append({
            "activityId": activity["id"],
            "validForUpload": bool(activity["clientId"]),
            "validationError": None if activity["clientId"] else "client GUID has not been synced",
            "payloadHash": _digest(_json(payload)),
            "payload": payload,
        })
    return values


def set_direct_upload_status(db, activity_ids, status):
    if status not in {"uploading", "uploaded", "failed"}:
        raise ValueError("direct upload status must be uploading, uploaded, or failed")
    ids = sorted({int(activity_id) for activity_id in activity_ids})
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    db.execute(
        f"DELETE FROM submission_attempts WHERE review_activity_id IN ({placeholders})",
        ids,
    )
    db.execute(
        f"""UPDATE review_activities
            SET status = ?, payload_hash = NULL, updated_at = ?
            WHERE id IN ({placeholders})""",
        [status, _now(), *ids],
    )


def prepare_submissions(db, day=None, target="tdc-internal-api", include_retryable=False):
    previews = preview_payloads(db, day, include_retryable)
    invalid = [value for value in previews if not value["validForUpload"]]
    if invalid:
        raise ValueError("all approved activities require a synced client GUID before upload preparation")
    prepared = []
    now = _now()
    for value in previews:
        idempotency_key = f"{value['payload']['clientActivityId']}:{value['payloadHash']}"
        db.execute(
            """INSERT OR IGNORE INTO submission_attempts
               (review_activity_id, target, idempotency_key, payload_version, payload_hash,
                payload_json, status, prepared_at)
               VALUES (?, ?, ?, ?, ?, ?, 'prepared', ?)""",
            (value["activityId"], target, idempotency_key, value["payload"]["schemaVersion"],
             value["payloadHash"], _json(value["payload"]), now),
        )
        row = db.execute(
            "SELECT * FROM submission_attempts WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        prepared.append(submission_view(row))
    return prepared


def submission_view(row):
    return {
        "id": row["id"],
        "activityId": row["review_activity_id"],
        "target": row["target"],
        "idempotencyKey": row["idempotency_key"],
        "payloadVersion": row["payload_version"],
        "payloadHash": row["payload_hash"],
        "payload": _loads(row["payload_json"], {}),
        "status": row["status"],
        "attemptCount": row["attempt_count"],
        "preparedAt": row["prepared_at"],
        "attemptedAt": row["attempted_at"],
        "completedAt": row["completed_at"],
        "httpStatus": row["http_status"],
        "receiptId": row["receipt_id"],
        "errorCode": row["error_code"],
        "errorMessage": row["error_message"],
    }


def list_submissions(db, status=None):
    query = "SELECT * FROM submission_attempts"
    values = []
    if status:
        if status not in SUBMISSION_STATUSES:
            raise ValueError("invalid submission status")
        query += " WHERE status = ?"
        values.append(status)
    query += " ORDER BY prepared_at DESC, id DESC"
    return [submission_view(row) for row in db.execute(query, values).fetchall()]


def update_submission(db, submission_id, status, http_status=None, receipt_id=None,
                      error_code=None, error_message=None):
    if status not in SUBMISSION_STATUSES:
        raise ValueError("status must be prepared, uploading, uploaded, or failed")
    row = db.execute("SELECT * FROM submission_attempts WHERE id = ?", (submission_id,)).fetchone()
    if not row:
        raise KeyError(f"submission #{submission_id} was not found")
    now = _now()
    attempted_at = row["attempted_at"]
    completed_at = row["completed_at"]
    attempt_count = row["attempt_count"]
    if status == "uploading":
        attempted_at = now
        attempt_count += 1
        completed_at = None
    elif status in {"uploaded", "failed"}:
        completed_at = now
    db.execute(
        """UPDATE submission_attempts SET status = ?, attempt_count = ?, attempted_at = ?,
             completed_at = ?, http_status = ?, receipt_id = ?, error_code = ?, error_message = ?
           WHERE id = ?""",
        (status, attempt_count, attempted_at, completed_at, http_status, receipt_id,
         error_code, str(error_message or "")[:1000] or None, submission_id),
    )
    if status == "uploaded":
        db.execute(
            "UPDATE review_activities SET updated_at = ? WHERE id = ?",
            (now, row["review_activity_id"]),
        )
    return submission_view(db.execute("SELECT * FROM submission_attempts WHERE id = ?", (submission_id,)).fetchone())


def review_summary(db, day=None):
    activities = list_activities(db, day)
    counts = Counter(activity["status"] for activity in activities)
    return {
        "date": day,
        "counts": dict(counts),
        "activeMinutes": sum(activity["activeMinutes"] for activity in activities
                             if activity["status"] not in {"excluded", "private"}),
        "activities": activities,
        "submissions": list_submissions(db),
    }
