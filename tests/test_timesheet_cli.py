import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "plugins" / "ai-activity-tracker-kit" / "scripts" / "ts.py"


class TimesheetCliTests(unittest.TestCase):
    def run_ts(self, home, *args, payload=None, storage="sqlite", config_path=None):
        env = os.environ.copy()
        if home is None:
            env.pop("TS_HOME", None)
        else:
            env["TS_HOME"] = str(home)
        if storage is None:
            env.pop("TS_STORAGE", None)
        else:
            env["TS_STORAGE"] = storage
        if config_path is not None:
            env["TS_CONFIG"] = str(config_path)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            self.fail(
                f"ts.py {' '.join(args)} failed with {result.returncode}:\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
        return result

    def test_sqlite_hook_creates_safe_pending_work_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            project = home / "project"
            project.mkdir()
            payload = {
                "hook_event_name": "PostToolUse",
                "session_id": "raw-session-id",
                "cwd": str(project),
                "prompt": "SECRET prompt content",
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(project / "src" / "app.py"),
                    "new_string": "SECRET file content",
                },
                "tool_response": "SECRET tool output",
            }

            self.run_ts(home, "hook", payload=payload)
            result = self.run_ts(home, "review", "--json")
            response_text = result.stdout
            entries = json.loads(response_text)["entries"]

            self.assertEqual(1, len(entries))
            self.assertEqual("pending", entries[0]["status"])
            self.assertEqual(str(project), entries[0]["workspace"])
            self.assertIn("src/app.py", entries[0]["summary"])
            self.assertNotIn("raw-session-id", response_text)
            self.assertNotIn("SECRET", response_text)

    def test_imported_tsv_preserves_existing_summary_calculation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            project = home / "project"
            project.mkdir()
            source = home / "legacy.log"
            first = int(datetime(2026, 11, 14, 9, 0).timestamp())
            second = int(datetime(2026, 11, 14, 9, 10).timestamp())
            original = (
                f"{first}\tUserPromptSubmit\traw-session\t{project}\n"
                f"{second}\tStop\traw-session\t{project}\n"
            )
            source.write_text(original, encoding="utf-8")
            (home / "clients.tsv").write_text(f"{project}\tTDC\n", encoding="utf-8")

            imported = self.run_ts(home, "import-tsv", str(source))
            result = self.run_ts(home, "summary", "2026-11-14", "--json")
            summary = json.loads(result.stdout)

            self.assertIn("Imported 2 event(s)", imported.stdout)
            self.assertEqual(2, summary["events"])
            self.assertEqual("TDC", summary["rows"][0]["client"])
            self.assertEqual(0.25, summary["rows"][0]["hours_raw"])
            self.assertEqual(original, source.read_text(encoding="utf-8"))

    def test_reviewed_entry_can_be_edited_approved_and_exported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            project = home / "project"
            project.mkdir()
            self.run_ts(home, "hook", payload={
                "hook_event_name": "Stop",
                "session_id": "session-one",
                "cwd": str(project),
            })
            review = json.loads(self.run_ts(home, "review", "--json").stdout)
            entry_id = str(review["entries"][0]["id"])

            before = json.loads(self.run_ts(home, "export", "--format", "json").stdout)
            updated = self.run_ts(
                home,
                "review",
                "--id",
                entry_id,
                "--summary",
                "Reviewed the ICE Context Kit SQLite design",
                "--client",
                "TDC",
                "--status",
                "approved",
                "--json",
            )
            exported = json.loads(self.run_ts(home, "export", "--format", "json").stdout)

            self.assertEqual([], before["entries"])
            updated_entry = json.loads(updated.stdout)["entries"][0]
            self.assertEqual("approved", updated_entry["status"])
            self.assertEqual("TDC", updated_entry["client"])
            self.assertEqual("Reviewed the ICE Context Kit SQLite design", updated_entry["summary"])
            self.assertEqual([updated_entry], exported["entries"])

    def test_unconfigured_hook_keeps_legacy_tsv_storage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            project = home / "project"
            project.mkdir()

            self.run_ts(
                home,
                "hook",
                payload={
                    "hook_event_name": "Stop",
                    "session_id": "legacy-session",
                    "cwd": str(project),
                },
                storage=None,
                config_path=home / "missing-config.json",
            )

            line = (home / "activity.log").read_text(encoding="utf-8").strip()
            self.assertEqual(4, len(line.split("\t")))
            self.assertIn("legacy-session", line)
            self.assertFalse((home / "activity.db").exists())

    def test_local_config_can_redirect_hooks_to_an_isolated_sqlite_trial(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            trial_home = root / "sqlite-trial"
            project = root / "project"
            project.mkdir()
            config = root / "timesheet-config.json"
            config.write_text(
                json.dumps({"home": str(trial_home), "storage": "sqlite"}),
                encoding="utf-8",
            )

            self.run_ts(
                None,
                "hook",
                payload={
                    "hook_event_name": "Stop",
                    "session_id": "trial-session",
                    "cwd": str(project),
                },
                storage=None,
                config_path=config,
            )
            reviewed = self.run_ts(
                None,
                "review",
                "--json",
                storage=None,
                config_path=config,
            )

            self.assertTrue((trial_home / "activity.db").exists())
            self.assertEqual(0o600, stat.S_IMODE((trial_home / "activity.db").stat().st_mode))
            self.assertEqual(1, len(json.loads(reviewed.stdout)["entries"]))

    def test_concurrent_hooks_are_all_available_to_the_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            project = home / "project"
            project.mkdir()

            def capture(index):
                return self.run_ts(home, "hook", payload={
                    "hook_event_name": "Stop",
                    "session_id": f"session-{index}",
                    "cwd": str(project),
                })

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(capture, range(12)))

            summary = json.loads(self.run_ts(home, "summary", "--json").stdout)
            self.assertEqual(12, summary["events"])

    def test_read_only_activity_gets_a_useful_safe_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            project = home / "project"
            project.mkdir()
            self.run_ts(home, "hook", payload={
                "hook_event_name": "PostToolUse",
                "session_id": "read-session",
                "cwd": str(project),
                "tool_name": "Read",
                "tool_input": {"file_path": str(project / "sensitive-name.txt")},
                "tool_response": "SECRET file contents",
            })

            response = self.run_ts(home, "review", "--json").stdout
            entry = json.loads(response)["entries"][0]

            self.assertEqual("Reviewed project files", entry["summary"])
            self.assertNotIn("sensitive-name.txt", response)
            self.assertNotIn("SECRET", response)

    def test_source_segments_and_stable_activity_id_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            project = home / "project"
            project.mkdir()
            day = "2026-11-14"
            first = int(datetime(2026, 11, 14, 9, 0).timestamp())

            self.run_ts(home, "review", day, "--json")
            with sqlite3.connect(home / "activity.db") as db:
                db.executemany(
                    """INSERT INTO activity_events
                       (occurred_at, event_name, session_key, workspace_path, source, tool_name,
                        edited_files_json)
                       VALUES (?, 'Stop', 'safe-session', ?, 'codex', 'command', '[]')""",
                    [(first, str(project)), (first + 60, str(project)), (first + 30 * 60, str(project))],
                )

            first_review = json.loads(self.run_ts(home, "review", day, "--json").stdout)["entries"][0]
            second_review = json.loads(self.run_ts(home, "review", day, "--json").stdout)["entries"][0]

            self.assertEqual(first_review["id"], second_review["id"])
            self.assertEqual("codex", first_review["source"])
            self.assertEqual(2, len(first_review["segments"]))

    def test_review_edit_after_approval_returns_activity_to_pending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            project = home / "project"
            project.mkdir()
            self.run_ts(home, "hook", "--source", "claude-code", payload={
                "hook_event_name": "Stop",
                "session_id": "session-one",
                "cwd": str(project),
            })
            entry = json.loads(self.run_ts(home, "review", "--json").stdout)["entries"][0]
            entry_id = str(entry["id"])
            self.run_ts(
                home, "review", "--id", entry_id, "--client", "TDC Internal",
                "--summary", "Reviewed local activity", "--status", "approved", "--json",
            )

            edited = json.loads(self.run_ts(
                home, "review", "--id", entry_id, "--summary", "Changed after approval", "--json",
            ).stdout)["entries"][0]

            self.assertEqual("pending", edited["status"])
            self.assertIsNone(edited["payload_hash"])
            with sqlite3.connect(home / "activity.db") as db:
                revision_count = db.execute(
                    "SELECT count(*) FROM review_revisions WHERE review_activity_id = ?",
                    (int(entry_id),),
                ).fetchone()[0]
            self.assertGreaterEqual(revision_count, 2)

    def test_client_guid_is_required_before_payload_preparation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            project = home / "project"
            project.mkdir()
            client_id = "11a6c04c-1547-4dba-86cb-8d3f9dd83881"
            clients_file = home / "clients.json"
            clients_file.write_text(json.dumps({"clients": [{
                "id": client_id,
                "name": "TDC Internal",
                "isActive": True,
            }]}), encoding="utf-8")
            self.run_ts(home, "clients", "--import", str(clients_file), "--replace")
            self.run_ts(home, "map", str(project), "TDC Internal", "--client-id", client_id)
            self.run_ts(home, "hook", "--source", "codex", payload={
                "hook_event_name": "Stop",
                "session_id": "session-one",
                "cwd": str(project),
            })
            entry = json.loads(self.run_ts(home, "review", "--json").stdout)["entries"][0]
            self.run_ts(
                home, "review", "--id", str(entry["id"]),
                "--summary", "Implemented local review workflow",
                "--status", "approved", "--json",
            )

            preview = json.loads(self.run_ts(home, "preview-upload").stdout)
            prepared = self.run_ts(home, "prepare-upload", "--yes")
            submissions = json.loads(self.run_ts(home, "submissions").stdout)["submissions"]

            self.assertEqual(client_id, preview["activities"][0]["payload"]["client"]["id"])
            self.assertTrue(preview["activities"][0]["validForUpload"])
            self.assertIn('"status": "prepared"', prepared.stdout)
            self.assertEqual("prepared", submissions[0]["status"])
            self.assertEqual(preview["activities"][0]["payloadHash"], submissions[0]["payloadHash"])

            submission_id = str(submissions[0]["id"])
            uploading = json.loads(self.run_ts(
                home, "submissions", "--id", submission_id, "--status", "uploading",
            ).stdout)["submission"]
            uploaded = json.loads(self.run_ts(
                home, "submissions", "--id", submission_id, "--status", "uploaded",
                "--http-status", "201", "--receipt-id", "receipt-123",
            ).stdout)["submission"]
            self.assertEqual(1, uploading["attemptCount"])
            self.assertEqual(201, uploaded["httpStatus"])
            self.assertEqual("receipt-123", uploaded["receiptId"])

    def test_client_catalogue_import_accepts_mcp_property_names_and_backfills_guid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            project = home / "project"
            project.mkdir()
            client_id = "11a6c04c-1547-4dba-86cb-8d3f9dd83881"
            self.run_ts(home, "map", str(project), "TDC Internal")
            self.run_ts(home, "hook", "--source", "codex", payload={
                "hook_event_name": "Stop",
                "session_id": "session-one",
                "cwd": str(project),
            })
            before = json.loads(self.run_ts(home, "review", "--json").stdout)["entries"][0]

            catalogue = {
                "Clients": [{
                    "ClientId": client_id,
                    "ClientName": "TDC Internal",
                    "IsActive": True,
                }]
            }
            imported = json.loads(self.run_ts(
                home, "clients", "--import", "-", "--replace", payload=catalogue,
            ).stdout)
            after = json.loads(self.run_ts(home, "review", "--json").stdout)["entries"][0]

            self.assertIsNone(before["client_id"])
            self.assertEqual(1, imported["imported"])
            self.assertEqual(client_id, after["client_id"])

    def test_lifecycle_deletion_removes_activity_and_raw_evidence_without_regeneration(self):
        scripts = SCRIPT.parent
        sys.path.insert(0, str(scripts))
        try:
            import review_store
        finally:
            sys.path.remove(str(scripts))

        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            projects = {}
            for name in ("excluded-project", "private-project", "uploaded-project"):
                project = home / name
                project.mkdir()
                projects[name] = project
                self.run_ts(home, "hook", "--source", "codex", payload={
                    "hook_event_name": "Stop",
                    "session_id": name,
                    "cwd": str(project),
                })

            entries = json.loads(self.run_ts(home, "review", "--json").stdout)["entries"]
            by_project = {entry["project"]: entry for entry in entries}
            self.run_ts(
                home, "review", "--id", str(by_project["excluded-project"]["id"]),
                "--status", "excluded", "--json",
            )
            self.run_ts(
                home, "review", "--id", str(by_project["private-project"]["id"]),
                "--status", "private", "--json",
            )

            client_id = "11a6c04c-1547-4dba-86cb-8d3f9dd83881"
            uploaded_id = str(by_project["uploaded-project"]["id"])
            self.run_ts(
                home, "review", "--id", uploaded_id,
                "--client", "TDC Internal", "--client-id", client_id,
                "--summary", "Uploaded activity", "--status", "approved", "--json",
            )
            self.run_ts(home, "prepare-upload", "--yes")
            submission = json.loads(self.run_ts(home, "submissions").stdout)["submissions"][0]
            self.run_ts(
                home, "submissions", "--id", str(submission["id"]), "--status", "uploading",
            )
            self.run_ts(
                home, "submissions", "--id", str(submission["id"]), "--status", "uploaded",
                "--http-status", "201", "--receipt-id", "receipt-123",
            )

            with sqlite3.connect(home / "activity.db") as db:
                db.row_factory = sqlite3.Row
                review_store.ensure_schema(db)
                self.assertEqual(
                    {"excluded": 1, "private": 1, "uploaded": 1},
                    review_store.deletion_counts(db),
                )

                excluded = review_store.delete_activities(db, "excluded")
                review_store.sync_all_review_activities(db, [], 15, 5)
                self.assertEqual(1, excluded["deletedActivities"])
                self.assertEqual(1, excluded["deletedEvents"])
                self.assertEqual(2, len(review_store.list_activities(db)))

                review_store.delete_activities(db, "private")
                review_store.delete_activities(db, "uploaded")
                review_store.sync_all_review_activities(db, [], 15, 5)
                self.assertEqual(0, len(review_store.list_activities(db)))
                self.assertEqual(0, db.execute("SELECT count(*) FROM activity_events").fetchone()[0])

    def test_segments_can_be_split_and_merged_without_changing_raw_events(self):
        scripts = SCRIPT.parent
        sys.path.insert(0, str(scripts))
        try:
            import review_store
        finally:
            sys.path.remove(str(scripts))
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            project = home / "project"
            project.mkdir()
            day = "2026-11-14"
            first = int(datetime(2026, 11, 14, 9, 0).timestamp())
            self.run_ts(home, "review", day, "--json")
            with sqlite3.connect(home / "activity.db") as db:
                db.row_factory = sqlite3.Row
                db.executemany(
                    """INSERT INTO activity_events
                       (occurred_at, event_name, session_key, workspace_path, source, tool_name,
                        edited_files_json)
                       VALUES (?, 'Stop', 'safe-session', ?, 'codex', 'command', '[]')""",
                    [(first, str(project)), (first + 30 * 60, str(project))],
                )
                raw_count = db.execute("SELECT count(*) FROM activity_events").fetchone()[0]
                review_store.sync_review_activities(db, day, [], 15, 5)
                activity = review_store.list_activities(db, day)[0]
                split = review_store.split_activity(
                    db, activity["id"], activity["segments"][0]["id"], "test"
                )
                merged = review_store.merge_activities(db, [item["id"] for item in split], "test")
                final_raw_count = db.execute("SELECT count(*) FROM activity_events").fetchone()[0]

            self.assertEqual(2, len(split))
            self.assertEqual(2, len(merged["segments"]))
            self.assertEqual(raw_count, final_raw_count)

    def test_unavailable_sqlite_storage_never_fails_the_hook(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            blocked_home = Path(temp_dir) / "not-a-directory"
            blocked_home.write_text("occupied", encoding="utf-8")

            result = self.run_ts(blocked_home, "hook", payload={
                "hook_event_name": "Stop",
                "session_id": "session-one",
                "cwd": temp_dir,
            })

            self.assertIn("dropped timesheet event", result.stderr)


if __name__ == "__main__":
    unittest.main()
