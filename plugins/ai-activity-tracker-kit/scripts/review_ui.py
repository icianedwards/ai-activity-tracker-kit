"""Localhost-only browser review UI for ICE Context Kit activities."""

import json
import secrets
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import review_store
import review_uploader


HTML_PATH = Path(__file__).with_name("review-ui.html")


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _error_payload(error):
    message = str(error)
    exception = error if isinstance(error, BaseException) else RuntimeError(message)
    technical_parts = []
    supplied_detail = getattr(exception, "technical_detail", None)
    if supplied_detail:
        technical_parts.append(supplied_detail)
    technical_parts.append(
        "Local stack trace:\n"
        + "".join(traceback.format_exception(
            type(exception), exception, exception.__traceback__
        )).rstrip()
    )
    return {
        "error": message,
        "technicalDetail": "\n\n".join(technical_parts),
    }


def run_server(open_db, load_map, day, gap_minutes, tail_minutes, port=0, open_browser=True,
               credential_path=None):
    token = secrets.token_urlsafe(32)
    uploader = review_uploader.ActivityUploader(
        credential_path or str(Path.home() / ".claude" / "timesheet" / "tdc-internal-oauth.json")
    )

    class ReviewHandler(BaseHTTPRequestHandler):
        server_version = "ContextKitReview/1.0"

        def log_message(self, format_value, *args):
            return

        def _headers(self, status, content_type, length):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:",
            )
            self.end_headers()

        def _send(self, value, status=200):
            body = _json_bytes(value)
            self._headers(status, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def _error(self, error, status=400):
            self._send(_error_payload(error), status)

        def _redirect(self, location):
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _authorised(self):
            return secrets.compare_digest(self.headers.get("X-Review-Token", ""), token)

        def _body(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000:
                raise ValueError("request body is too large")
            if not length:
                return {}
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("request body must be a JSON object")
            return value

        def _state(self):
            with open_db() as db:
                review_store.sync_all_review_activities(
                    db, load_map(), gap_minutes, tail_minutes
                )
                activities = review_store.list_activities(db)
                clients = review_store.list_clients(db)
                previews = review_store.preview_payloads(db, include_retryable=True)
                raw_count = db.execute("SELECT count(*) FROM activity_events").fetchone()[0]
                counts = review_store.deletion_counts(db)
            return {
                "activities": activities,
                "clients": clients,
                "uploadPreview": previews,
                "rawEventCount": raw_count,
                "deletionCounts": counts,
                "payloadVersion": review_store.PAYLOAD_VERSION,
            }

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/oauth/callback":
                values = urllib.parse.parse_qs(parsed.query)
                try:
                    if values.get("error"):
                        raise review_uploader.UploadError(values.get("error_description", values["error"])[0])
                    uploader.complete_authorization(
                        values.get("code", [""])[0], values.get("state", [""])[0]
                    )
                    self._redirect(f"/?token={urllib.parse.quote(token)}&upload=1")
                except Exception as error:
                    self._error(error)
                return
            if parsed.path == "/":
                body = HTML_PATH.read_bytes()
                self._headers(200, "text/html; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if not self._authorised():
                self._error("invalid review session token", 403)
                return
            if parsed.path == "/api/state":
                try:
                    self._send(self._state())
                except Exception as error:
                    self._error(error, 500)
                return
            if parsed.path == "/api/auth/start":
                try:
                    callback = f"http://127.0.0.1:{self.server.server_port}/oauth/callback"
                    self._send({"authorizationUrl": uploader.authorization_url(callback)})
                except Exception as error:
                    self._error(error, 500)
                return
            self._error("not found", 404)

        def do_PATCH(self):
            if not self._authorised():
                self._error("invalid review session token", 403)
                return
            parts = urllib.parse.urlparse(self.path).path.strip("/").split("/")
            if len(parts) != 3 or parts[:2] != ["api", "activities"]:
                self._error("not found", 404)
                return
            try:
                activity_id = int(parts[2])
                with open_db() as db:
                    activity = review_store.update_activity(db, activity_id, self._body())
                self._send({"activity": activity})
            except KeyError as error:
                self._error(error, 404)
            except Exception as error:
                self._error(error)

        def do_POST(self):
            if not self._authorised():
                self._error("invalid review session token", 403)
                return
            path = urllib.parse.urlparse(self.path).path
            parts = path.strip("/").split("/")
            try:
                body = self._body()
                if len(parts) == 4 and parts[:2] == ["api", "activities"] and parts[3] == "status":
                    with open_db() as db:
                        activity = review_store.set_activity_status(
                            db, int(parts[2]), str(body.get("status", ""))
                        )
                    self._send({"activity": activity})
                    return
                if len(parts) == 4 and parts[:2] == ["api", "activities"] and parts[3] == "split":
                    with open_db() as db:
                        activities = review_store.split_activity(
                            db, int(parts[2]), int(body.get("afterSegmentId"))
                        )
                    self._send({"activities": activities})
                    return
                if path == "/api/activities/merge":
                    with open_db() as db:
                        activity = review_store.merge_activities(db, body.get("activityIds", []))
                    self._send({"activity": activity})
                    return
                if path == "/api/upload":
                    try:
                        uploader.require_access_token()
                    except review_uploader.AuthenticationRequired:
                        self._send({"error": "TDC Internal sign-in is required.", "requiresAuthentication": True}, 401)
                        return
                    with open_db() as db:
                        previews = review_store.preview_payloads(db, include_retryable=True)
                        if not previews:
                            raise ValueError("no approved activities are available to upload")
                        activity_ids = [preview["activityId"] for preview in previews]
                        review_store.set_direct_upload_status(db, activity_ids, "uploading")
                    try:
                        result = uploader.upload([preview["payload"] for preview in previews])
                    except Exception as error:
                        with open_db() as db:
                            review_store.set_direct_upload_status(db, activity_ids, "failed")
                        raise
                    with open_db() as db:
                        review_store.set_direct_upload_status(db, activity_ids, "uploaded")
                    self._send({"result": result})
                    return
                if path == "/api/delete":
                    with open_db() as db:
                        result = review_store.delete_activities(db, str(body.get("category") or ""))
                    self._send(result)
                    return
                if path == "/api/shutdown":
                    self._send({"stopping": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                self._error("not found", 404)
            except KeyError as error:
                self._error(error, 404)
            except Exception as error:
                self._error(error)

    server = ThreadingHTTPServer(("127.0.0.1", port), ReviewHandler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}/?token={urllib.parse.quote(token)}"
    print(f"ICE Context Kit review is available at {url}")
    print("It is local-only. Press Ctrl-C in this terminal to stop it.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

