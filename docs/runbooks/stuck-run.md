# A run that does not move

A project in the Studio is one pipeline run (a Temporal workflow on the `pipeline` task
queue). Most of the time a run that "does not move" is not stuck: it is waiting for the
owner. Work through this list in order; stop at the first answer.

## 1. Is it waiting for you?

The Studio shows the reason on the project. The usual ones:

| The Studio says | It waits for | What to do |
| --- | --- | --- |
| waiting for songs | WAV files in the Flow Music / Suno inbox | Make the songs from the prompt sheets in the inbox and save them there; then Continue (on that project only). |
| waiting for thumbnail art (`THUMBNAIL_ART_AWAITED`) | `A.png`, `B.png`, `C.png` in the thumbnail inbox | Save them as `<package>\PROMPTS.md` asks, or `plm media thumbnail-import <package>`. |
| waiting for publish approval (`PUBLISH_APPROVAL_AWAITED`) | your review | Watch the video in the review, then Approve or ask for a change. |
| a hold with a reason code (a refused step, short supply, the spending guard) | your decision | Read the reason. Try again only when its cause is fixed; otherwise cancel the project. |

A grant that expired (`PLM_WRITE_AUTHORIZATIONS_FILE`) shows as a spending-guard hold:
renew the grant, then try again.

## 2. Is the system up?

    .\.venv\Scripts\python.exe -m plm_platform.cli doctor

- `worker_pollers` FAIL: the pipeline worker is not polling. `docker compose ps` in the
  main checkout; if `pipeline-worker` is not healthy, `docker compose logs --tail 200
  pipeline-worker`. Containers restart by themselves (`restart: unless-stopped`); start
  the stack with `PLM_Studio.vbs`, which only runs `docker compose up` when something is
  missing or unhealthy.
- A phase API FAIL: the step calling it retries until the API is back. Look at that
  container's log.
- Disk full: docs/runbooks/full-disk.md.

Never run `docker compose` from a git worktree without its own `COMPOSE_PROJECT_NAME`:
it would replace the live containers with that worktree's code.

## 3. What is the workflow doing?

Temporal UI: http://127.0.0.1:8080, namespace `default`, find the workflow id the Studio
shows for the project.

- **Pending activity, attempt count climbing**: a step fails and retries. Its last
  failure is in the activity's details and in that phase's container log. Fix the cause;
  the next attempt picks it up. Do not reset the workflow.
- **Waiting for a signal**: it is a hold (section 1).
- **A render (P6 `assemble`) running for long**: a 1440p two-hour programme takes tens of
  minutes. `docker stats plm-api-media-1` shows FFmpeg working. Do not restart
  `api-media` during a render (its stop grace period is 120 s, a render is longer).

## 4. Stop it

Cancel the project in the Studio: the run is cancelled, nothing more is made or sent.
Something already public on YouTube stays public; remove it in YouTube Studio if needed.

Do **not** use "Reset" or "Terminate" in the Temporal UI: a reset replays steps under
new keys (a second upload is possible) and terminate skips the run's own clean-up.
