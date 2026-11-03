# Queue full-auto runner

The bridge drains `queue.txt` one YouTube URL at a time. The public page keeps showing that URL until the child exits. Publishing the page stays on its usual period, and a removal is included in the next publish.

## Command

Working directory is the `interview_helper_mux` checkout (`product_dir`). The interpreter is that checkout's `.venv/bin/python`.

```bash
.venv/bin/python -m interview_mux orchestrate \
  --mode full-auto \
  --url 'https://www.youtube.com/watch?v=VIDEO_ID'
```

The portal starts that command and waits for the process to exit. A restart starts it again from the beginning. The portal does not pass `--run-id`.

The child inherits the bridge environment so the product can find its tools. `PYTHONPATH`, `MUX_*`, and AWS credential variables are removed first. Portal secrets stay inside the publisher.

Exit **0** is success. Any other exit is a failure. There is one attempt. Adding the same URL again later is a new attempt.

## Order

- The first line of `queue.txt` is the episode that runs.
- After that child exits on its own, the URL is removed and the bridge waits `orchestrate_gap_seconds` (5) before starting whatever is now first.
- The wait applies between items. An empty queue starts the next accepted URL without an extra wait.
- A line that is not a YouTube URL is recorded as a failure and removed without starting the product.
- When `.venv/bin/python` is missing, the bridge logs that once and leaves the queue unchanged.

## Records

Each attempt gets a folder under `ASSETS/orchestrations/{utc-stamp}-{video_id}-{suffix}/`:

- `request.json` — URL, product argv, working directory, start time
- `stdout.log` and `stderr.log` — the child output
- `result.json` — outcome, return code, signal, reason, end time, and whether the URL was removed
- `exit_code.txt` — the product process exit code, written so a later portal process can read it

`ASSETS/orchestrations/current.json` holds the living child pid and the URL. If the portal is killed hard and that pid is still alive, the next start adopts it and does not launch a second episode. When that adopted child exits, success removes the URL and any other exit is one recorded failure, then the URL is removed.

## Stopping the portal

A normal stop ends the child, writes `result.json` with outcome `interrupted`, and leaves the URL at the front of `queue.txt`. The next `./scripts/run.sh` finds no living pid and starts a new episode for that URL.
