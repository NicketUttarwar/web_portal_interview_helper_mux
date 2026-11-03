"""Commands used by scripts/run.sh."""

from __future__ import annotations

import argparse
import sys

from portal.bridge import format_status, serve
from portal.config import load_settings
from portal.store import enqueue


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="./scripts/run.sh",
        description="Run the local queue and publish it to the site.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="Stay in this terminal and keep the queues in step.")
    sub.add_parser("status", help="Print the local queue, including work not yet claimed.")
    add = sub.add_parser("enqueue", help="Add one job to the local queue.")
    add.add_argument("kind", help="Allowlisted kind. echo is the handler that runs.")
    add.add_argument(
        "payload",
        nargs=argparse.REMAINDER,
        help="Text stored with the job. It is not published on the website.",
    )
    args = parser.parse_args(argv)
    settings = load_settings()
    command = args.command or "run"
    if command == "run":
        return serve(settings)
    if command == "status":
        print(format_status(settings))
        return 0
    if command == "enqueue":
        payload = " ".join(args.payload).strip()
        try:
            job_id = enqueue(settings, args.kind, payload)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"Queued {job_id} ({args.kind}).")
        print("The running bridge claims it. If the bridge is stopped, it claims it on the next start.")
        return 0
    parser.print_help()
    return 2
