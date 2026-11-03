#!/usr/bin/env bash
# The one command for this repository.
#
#   ./scripts/run.sh                         Stay in this terminal. Keep the local
#                                            queue and the website queue in step.
#                                            After a YouTube URL is accepted, a
#                                            child process looks up its title and
#                                            writes that title back onto the queue.
#                                            The first URL also starts full-auto
#                                            in interview_helper_mux. When that
#                                            child exits, the URL leaves the queue.
#   ./scripts/run.sh enqueue echo hello      Add one job. The bridge claims it.
#   ./scripts/run.sh status                  Print the local queue.
#
# Stopping the process is safe. Start it again later. While it is stopped, the
# website keeps the last queue page that uploaded.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v python3.12 >/dev/null 2>&1; then
  echo "python3.12 is required and was not found on PATH." >&2
  exit 127
fi

VENV="$ROOT/.venv"
PY="$VENV/bin/python"
LOCK="$ROOT/requirements.lock"
STAMP="$VENV/.requirements.stamp"

create=0
if [[ ! -x "$PY" ]]; then
  create=1
elif ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)'; then
  echo "Replacing .venv because it is not Python 3.12."
  rm -rf "$VENV"
  create=1
fi

if [[ "$create" -eq 1 ]]; then
  echo "Creating .venv with python3.12"
  python3.12 -m venv "$VENV"
fi

lock_hash="$(shasum -a 256 "$LOCK" | awk '{print $1}')"
if [[ ! -f "$STAMP" || "$(cat "$STAMP")" != "$lock_hash" ]]; then
  echo "Installing the lockfile into .venv"
  "$PY" -m pip install --disable-pip-version-check --upgrade pip
  "$PY" -m pip install --disable-pip-version-check -r "$LOCK"
  printf '%s\n' "$lock_hash" > "$STAMP"
fi

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" -m portal "$@"
