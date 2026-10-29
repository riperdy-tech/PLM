"""OAuth 2.0 authorization helper for YouTube channel publishing."""

import http.server
import socketserver
import sys
import threading
import urllib.parse
from pathlib import Path

import httpx

sys.stdout.reconfigure(line_buffering=True)

PORT = 8090
REDIRECT_URI = f"http://127.0.0.1:{PORT}/"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    auth_code: str | None = None
    error: str | None = None

    def do_GET(self) -> None:
        url = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(url.query)

        if "code" in params:
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
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"Authorization error: {OAuthCallbackHandler.error}".encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        # Silence server request logs
        return


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

    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)

    server = socketserver.TCPServer(("127.0.0.1", PORT), OAuthCallbackHandler)
    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    print("\n" + "=" * 65)
    print("YOUTUBE OAUTH AUTHORIZATION FOR 'Rhodes & Rain Records'")
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
        print(f"Token exchange failed (HTTP {token_resp.status_code}): {token_resp.text}")
        return

    data = token_resp.json()
    refresh_token = data.get("refresh_token")
    access_token = data.get("access_token")

    if not refresh_token:
        print("Warning: Google did not return a refresh_token (was prompt=consent used?). Full response:", data)
        return

    print("SUCCESS! Refresh token obtained.")

    # Save to .env
    lines = env_content.splitlines()
    new_lines = []
    found = False
    for line in lines:
        if line.startswith("YOUTUBE_OAUTH_REFRESH_TOKEN="):
            new_lines.append(f"YOUTUBE_OAUTH_REFRESH_TOKEN={refresh_token}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"YOUTUBE_OAUTH_REFRESH_TOKEN={refresh_token}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
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
