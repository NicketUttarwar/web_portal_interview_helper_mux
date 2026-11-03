"""Write the job payload into the job folder and exit.

This is the first allowlisted handler. It does not read the environment for
credentials and it does not take a command from the payload.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m portal.handlers.echo JOB_DIR", file=sys.stderr)
        return 2
    job_dir = Path(args[0])
    request = json.loads((job_dir / "request.json").read_text(encoding="utf-8"))
    payload = request.get("payload", "")
    if not isinstance(payload, str):
        print("payload must be a string", file=sys.stderr)
        return 2
    (job_dir / "payload.txt").write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
