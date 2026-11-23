"""OAuth and direct activity upload support for the local ICE Context Kit review UI."""

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_API_BASE = "https://internal-api.tdc.co.za"
DEFAULT_SCOPE = "openid profile email mcp offline_access"


class AuthenticationRequired(Exception):
    """Raised when the user must complete the browser sign-in flow."""


class UploadError(Exception):
    """Raised when TDC Internal rejects or cannot accept an upload."""

    def __init__(self, message, technical_detail=None):
        super().__init__(message)
        self.technical_detail = technical_detail


def _base64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class ActivityUploader:
    def __init__(self, credential_path, api_base=None, timeout_seconds=30):
        self.credential_path = os.path.expanduser(credential_path)
        self.api_base = str(api_base or os.environ.get("TDC_INTERNAL_API_URL") or DEFAULT_API_BASE).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.credentials = self._load_credentials()
        self.pending_authorizations = {}

    def authorization_url(self, callback_url):
        registration = self._request_json(
            "/connect/register",
            {
                "client_name": "ICE Context Kit local activity review",
                "redirect_uris": [callback_url],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )
        client_id = registration.get("client_id")
        if not client_id:
            raise UploadError("TDC Internal did not return an OAuth client id.")

        verifier = secrets.token_urlsafe(64)
        challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
        state = secrets.token_urlsafe(32)
        scope = registration.get("scope") or DEFAULT_SCOPE
        self.pending_authorizations[state] = {
            "client_id": client_id,
            "callback_url": callback_url,
            "verifier": verifier,
        }
        query = urllib.parse.urlencode({
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": callback_url,
            "scope": scope,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        })
        return f"{self.api_base}/connect/authorize?{query}"

    def complete_authorization(self, code, state):
        pending = self.pending_authorizations.pop(state, None)
        if not pending:
            raise UploadError("The sign-in request has expired. Return to the review page and try again.")

        token = self._request_form(
            "/connect/token",
            {
                "grant_type": "authorization_code",
                "client_id": pending["client_id"],
                "code": code,
                "redirect_uri": pending["callback_url"],
                "code_verifier": pending["verifier"],
            },
        )
        self._store_token(token, pending["client_id"])

    def require_access_token(self):
        access_token = self.credentials.get("access_token")
        expires_at = int(self.credentials.get("expires_at") or 0)
        if access_token and expires_at > int(time.time()) + 60:
            return access_token

        refresh_token = self.credentials.get("refresh_token")
        client_id = self.credentials.get("client_id")
        if not refresh_token or not client_id:
            raise AuthenticationRequired("Sign in to TDC Internal to upload activities.")

        try:
            token = self._request_form(
                "/connect/token",
                {
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                },
            )
        except UploadError as error:
            self.credentials = {}
            self._save_credentials()
            raise AuthenticationRequired("Your TDC Internal sign-in has expired.") from error

        self._store_token(token, client_id)
        return self.credentials["access_token"]

    def upload(self, payloads):
        token = self.require_access_token()
        response = self._request_json(
            "/api/timesheet/activities",
            {"activities": payloads},
            headers={"Authorization": f"Bearer {token}"},
        )
        if not response.get("success"):
            raise UploadError(
                response.get("message") or response.get("Message")
                or "TDC Internal rejected the activity upload.",
                "API response:\n" + json.dumps(response, indent=2, ensure_ascii=False),
            )
        return response.get("data") or {}

    def _store_token(self, token, client_id):
        access_token = token.get("access_token")
        if not access_token:
            raise UploadError("TDC Internal did not return an access token.")
        self.credentials = {
            "client_id": client_id,
            "access_token": access_token,
            "refresh_token": token.get("refresh_token") or self.credentials.get("refresh_token"),
            "expires_at": int(time.time()) + int(token.get("expires_in") or 3600),
        }
        self._save_credentials()

    def _load_credentials(self):
        try:
            with open(self.credential_path, encoding="utf-8") as stream:
                value = json.load(stream)
                return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_credentials(self):
        folder = os.path.dirname(self.credential_path)
        os.makedirs(folder, mode=0o700, exist_ok=True)
        temporary = self.credential_path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(self.credentials, stream)
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.credential_path)

    def _request_json(self, path, body, headers=None):
        request_headers = {"Content-Type": "application/json", "Accept": "application/json"}
        request_headers.update(headers or {})
        request = urllib.request.Request(
            self.api_base + path,
            data=json.dumps(body).encode("utf-8"),
            headers=request_headers,
            method="POST",
        )
        return self._execute(request)

    def _request_form(self, path, body):
        request = urllib.request.Request(
            self.api_base + path,
            data=urllib.parse.urlencode(body).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )
        return self._execute(request)

    def _execute(self, request):
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response_body = response.read().decode("utf-8", errors="replace")
                try:
                    return json.loads(response_body)
                except ValueError as error:
                    detail = self._http_detail(
                        request,
                        response.status,
                        getattr(response, "reason", ""),
                        response_body,
                        type(error).__name__,
                    )
                    raise UploadError("TDC Internal returned an invalid JSON response.", detail) from error
        except urllib.error.HTTPError as error:
            response_body = error.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(response_body)
                message = (
                    payload.get("message") or payload.get("Message")
                    or payload.get("error_description") or payload.get("error")
                    or payload.get("title") or payload.get("Title")
                )
            except (ValueError, AttributeError):
                message = None
            detail = self._http_detail(
                request,
                error.code,
                error.reason,
                response_body,
                type(error).__name__,
            )
            raise UploadError(message or f"TDC Internal returned HTTP {error.code}.", detail) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            detail = "\n".join([
                f"Exception: {type(error).__name__}",
                f"Request: {request.get_method()} {request.full_url}",
                f"Reason: {error!r}",
            ])
            raise UploadError(f"Could not connect to TDC Internal: {error}", detail) from error

    @staticmethod
    def _http_detail(request, status, reason, response_body, exception_name):
        return "\n".join([
            f"Exception: {exception_name}",
            f"Request: {request.get_method()} {request.full_url}",
            f"Status: {status} {reason}".rstrip(),
            "Response body:",
            response_body or "(empty)",
        ])
