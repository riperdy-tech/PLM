# YouTube OAuth: authorize, rotate, revoke

P7 (uploads, thumbnails, playlists) and P9 (YouTube Analytics) sign in to the channel
with one OAuth refresh token, `YOUTUBE_OAUTH_REFRESH_TOKEN` in the main checkout's `.env`.
The client id and secret (`YOUTUBE_OAUTH_CLIENT_ID`, `YOUTUBE_OAUTH_CLIENT_SECRET`) come
from the Google Cloud project's OAuth client. None of these is ever typed into chat, a
commit, a log or a ticket.

## When to rotate

- An upload or analytics call fails with `invalid_grant` or HTTP 401 (the token was
  revoked, expired, or the Google account's password changed).
- The token may have been exposed (pasted somewhere, a copy of `.env` left a machine).
- The OAuth client secret was rotated in Google Cloud.

A Google Cloud project in "Testing" publishing status issues refresh tokens that expire
after 7 days; a project "In production" does not.

## Rotate

1. Make sure no upload is running (Studio: no project at the upload step).
2. In the main checkout (`C:\Users\riper\Downloads\PLM`), in PowerShell:

       .\.venv\Scripts\python.exe scripts\authorize_youtube.py

3. Open the printed URL, sign in with the channel's Google account, allow the three
   scopes. The browser tab says "Authorization Successful"; the terminal prints the
   channel name it is now signed in as. Check that it is the right channel.
   - "state does not match": the callback came from another (older) tab or request.
     Run the script again and use only the URL it just printed.
   - "Token exchange failed (HTTP 400): invalid_grant": the code was used or is older
     than a few minutes. Run it again.
   - "did not return a refresh_token": remove PLM's access in the Google account
     (step 1 of "Revoke") and run it again.
4. The script replaces `YOUTUBE_OAUTH_REFRESH_TOKEN` in `.env` in one step (it never
   prints the token). Compose passes `.env` values to the containers when it creates
   them (the containers never read `.env` themselves), so recreate the two services that
   use the token; a plain `docker compose restart` would keep the old one. In the main
   checkout:

       docker compose up -d api-youtube api-analytics

5. Check: `plm doctor` passes, and the next daily report
   (`scripts/channel_daily_report.py`) reads analytics.

## Revoke

1. Google account, Security, "Third-party apps with account access": remove the PLM
   app. Every refresh token it holds stops working at once.
2. Delete the `YOUTUBE_OAUTH_REFRESH_TOKEN` line from `.env` (P7 and P9 then refuse
   real calls instead of failing at Google).
3. If the client secret may be exposed too, create a new secret for the OAuth client in
   Google Cloud, put it in `.env`, delete the old one, then authorize again.
