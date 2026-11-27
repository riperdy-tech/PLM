"""OAuth 2.0 authorization helper for YouTube channel publishing.

Writes ``YOUTUBE_OAUTH_REFRESH_TOKEN`` into ``.env`` (atomically) and never prints a
token or a token response. The callback must carry the ``state`` this run sent.
See docs/runbooks/oauth-rotation.md.
"""

import http.server
import io
import os
import secrets
import socketserver
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path

import httpx

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(line_buffering=True)

PORT = 8090
REDIRECT_URI = f"http://127.0.0.1:{PORT}/"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    auth_code: str | None = None
    error: str | None = None
    expected_state: str = ""

    def do_GET(self) -> None:
        url = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(url.query)
        state = params.get("state", [""])[0]

        if ("code" in params or "error" in params) and not secrets.compare_digest(
            state, OAuthCallbackHandler.expected_state
        ):
            # Not the answer to this run's request (CSRF or a stale tab): refuse the code.
            OAuthCallbackHandler.error = "state_mismatch"
            self.send_response(400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Authorization refused: state does not match this request.")
        elif "code" in params:
            OAuthCallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>Authorization Successful!</h1>"
                b"<p>PLM has received your YouTube channel authorization. "
                b"You can close this browser tab and return to the terminal.</p></body></html>"
            )
        elif "error" in params:
            OAuthCallbackHandler.error = params["error"][0]
            self.send_response(400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Authorization error; see the terminal.")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        # Silence server request logs
        return


def _write_env_atomically(env_path: Path, text: str) -> None:
    """Replace ``.env`` in one step: a crash leaves the old file or the new one, never half."""
    fd, temp = tempfile.mkstemp(dir=env_path.resolve().parent, prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, env_path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _with_refresh_token(env_content: str, refresh_token: str) -> str:
    new_lines = []
    found = False
    for line in env_content.splitlines():
        if line.startswith("YOUTUBE_OAUTH_REFRESH_TOKEN="):
            new_lines.append(f"YOUTUBE_OAUTH_REFRESH_TOKEN={refresh_token}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"YOUTUBE_OAUTH_REFRESH_TOKEN={refresh_token}")
    return "\n".join(new_lines) + "\n"


def main() -> None:
    env_path = Path(".env")
    env_content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""

    client_id = None
    client_secret = None
    for line in env_content.splitlines():
        if line.startswith("YOUTUBE_OAUTH_CLIENT_ID="):
            client_id = line.split("=", 1)[1].strip()
        elif line.startswith("YOUTUBE_OAUTH_CLIENT_SECRET="):
            client_secret = line.split("=", 1)[1].strip()

    if not client_id or not client_secret:
        print("Error: YOUTUBE_OAUTH_CLIENT_ID or YOUTUBE_OAUTH_CLIENT_SECRET not found in .env")
        return

    state = secrets.token_urlsafe(32)
    OAuthCallbackHandler.expected_state = state
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)

    server = socketserver.TCPServer(("127.0.0.1", PORT), OAuthCallbackHandler)
    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    print("\n" + "=" * 65)
    print("YOUTUBE OAUTH AUTHORIZATION FOR THE PLM CHANNEL")
    print("=" * 65)
    print("Please open the following URL in your browser to authorize PLM:")
    print(f"\n{auth_url}\n")
    print("Waiting for callback on port 8090...")

    server_thread.join(timeout=180)
    server.server_close()

    if not OAuthCallbackHandler.auth_code:
        print(f"Authorization timed out or failed: {OAuthCallbackHandler.error}")
        return

    print("\nAuthorization code received! Exchanging for permanent refresh token...")

    token_resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": OAuthCallbackHandler.auth_code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
    )

    if token_resp.status_code != 200:
        # Only Google's error code: never the body, which can carry request details.
        try:
            code = str(token_resp.json().get("error", "unknown"))
        except ValueError:
            code = "unreadable response"
        print(f"Token exchange failed (HTTP {token_resp.status_code}): {code}")
        return

    data = token_resp.json()
    refresh_token = data.get("refresh_token")
    access_token = data.get("access_token")

    if not refresh_token:
        print("Warning: Google did not return a refresh_token (was prompt=consent used?). "
              f"Response fields: {sorted(data)}")
        return

    print("SUCCESS! Refresh token obtained.")

    # Re-read .env: it may have changed during the up-to-3-minute wait.
    current = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    _write_env_atomically(env_path, _with_refresh_token(current, refresh_token))
    print("Saved YOUTUBE_OAUTH_REFRESH_TOKEN to .env!")

    # Verify channel details
    if access_token:
        ch_resp = httpx.get(
            "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if ch_resp.status_code == 200:
            ch_data = ch_resp.json()
            items = ch_data.get("items", [])
            if items:
                ch = items[0]["snippet"]
                print(f"Authenticated as Channel: {ch.get('title')} ({items[0].get('id')})")


if __name__ == "__main__":
    main()
