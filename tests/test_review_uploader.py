import json
import os
import tempfile
import unittest
import urllib.parse
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "plugins" / "ai-activity-tracker-kit" / "scripts"
import sys
sys.path.insert(0, str(SCRIPT_DIR))

from review_uploader import ActivityUploader, AuthenticationRequired, UploadError
from review_ui import _error_payload


class FakeUploader(ActivityUploader):
    def __init__(self, credential_path):
        super().__init__(credential_path, api_base="https://internal.example")
        self.json_requests = []
        self.form_requests = []

    def _request_json(self, path, body, headers=None):
        self.json_requests.append((path, body, headers or {}))
        if path == "/connect/register":
            return {"client_id": "client-1", "scope": "openid profile email mcp offline_access"}
        if path == "/api/timesheet/activities":
            return {"success": True, "data": {"uploaded": len(body["activities"])}}
        raise AssertionError(path)

    def _request_form(self, path, body):
        self.form_requests.append((path, body))
        return {
            "access_token": "refreshed-access",
            "refresh_token": "refreshed-token",
            "expires_in": 3600,
        }


class ActivityUploaderTests(unittest.TestCase):
    def test_plain_text_server_error_has_copyable_technical_detail(self):
        payload = _error_payload("invalid review session token")

        self.assertEqual("invalid review session token", payload["error"])
        self.assertIn("RuntimeError: invalid review session token", payload["technicalDetail"])

    def test_authorization_url_uses_pkce_and_mcp_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            uploader = FakeUploader(os.path.join(folder, "auth.json"))

            url = uploader.authorization_url("http://127.0.0.1:1234/oauth/callback")
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

            self.assertEqual(["client-1"], query["client_id"])
            self.assertEqual(["S256"], query["code_challenge_method"])
            self.assertIn("mcp", query["scope"][0].split())
            self.assertIn(query["state"][0], uploader.pending_authorizations)

    def test_upload_reuses_valid_access_token(self):
        with tempfile.TemporaryDirectory() as folder:
            uploader = FakeUploader(os.path.join(folder, "auth.json"))
            uploader.credentials = {
                "client_id": "client-1",
                "access_token": "current-access",
                "refresh_token": "refresh",
                "expires_at": 4102444800,
            }

            result = uploader.upload([{"clientActivityId": "one"}])

            self.assertEqual(1, result["uploaded"])
            self.assertEqual("Bearer current-access", uploader.json_requests[-1][2]["Authorization"])
            self.assertEqual([], uploader.form_requests)

    def test_missing_credentials_require_sign_in(self):
        with tempfile.TemporaryDirectory() as folder:
            uploader = FakeUploader(os.path.join(folder, "auth.json"))

            with self.assertRaises(AuthenticationRequired):
                uploader.require_access_token()

    def test_refresh_token_is_saved_with_owner_only_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "auth.json")
            uploader = FakeUploader(path)
            uploader.credentials = {
                "client_id": "client-1",
                "refresh_token": "refresh",
                "expires_at": 0,
            }

            token = uploader.require_access_token()

            self.assertEqual("refreshed-access", token)
            self.assertEqual("refreshed-token", json.loads(Path(path).read_text())["refresh_token"])
            self.assertEqual(0o600, os.stat(path).st_mode & 0o777)

    def test_http_error_preserves_endpoint_status_and_response_body(self):
        with tempfile.TemporaryDirectory() as folder:
            uploader = ActivityUploader(
                os.path.join(folder, "auth.json"),
                api_base="https://internal.example",
            )
            response = BytesIO(b'{"StatusCode":404,"Message":"Endpoint not found","TraceId":"abc-123"}')
            http_error = urllib.error.HTTPError(
                "https://internal.example/api/timesheet/activities",
                404,
                "Not Found",
                {},
                response,
            )

            with patch("urllib.request.urlopen", side_effect=http_error):
                with self.assertRaises(UploadError) as raised:
                    uploader._request_json("/api/timesheet/activities", {"activities": []})

            self.assertEqual("Endpoint not found", str(raised.exception))
            self.assertIn("POST https://internal.example/api/timesheet/activities", raised.exception.technical_detail)
            self.assertIn("Status: 404 Not Found", raised.exception.technical_detail)
            self.assertIn('"TraceId":"abc-123"', raised.exception.technical_detail)
            http_error.close()


if __name__ == "__main__":
    unittest.main()
