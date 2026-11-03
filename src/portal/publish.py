"""Render the public queue page and upload that one object."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from portal.queue_file import append_queue, read_queue, youtube_url
from portal.store import enqueue

from portal.config import MAX_PAYLOAD_CHARS, Settings, read_secrets, read_tf_assignment
from portal.models import Job

MAX_INTAKE_BYTES = min(MAX_PAYLOAD_CHARS, 100_000)
_S3_CONFIG = Config(signature_version="s3v4", s3={"addressing_style": "virtual"})


def _intake_prefix(run_id: str) -> str:
    return f"intake/{run_id}/"

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Queue — Zero Shot Media</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{
    margin: 0; background: #070910; color: #e6ecf6;
    font: 16px/1.5 "Avenir Next", "Segoe UI", sans-serif;
  }}
  main {{ max-width: 920px; margin: 0 auto; padding: 48px 20px 80px; }}
  a {{ color: #6ef0ff; }}
  h1 {{ font-size: 2.4rem; letter-spacing: -0.04em; margin: 24px 0 8px; }}
  p {{ color: #a3b0c7; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 28px; }}
  th, td {{ text-align: left; padding: 12px 8px; border-bottom: 1px solid #1c2433; vertical-align: top; }}
  th {{ color: #5a6478; font-size: 0.75rem; letter-spacing: 0.14em; text-transform: uppercase; }}
  .status {{ text-transform: uppercase; letter-spacing: 0.08em; font-size: 0.78rem; }}
</style>
</head>
<body>
<main>
  <p><a href="/">Zero Shot Media</a></p>
  <h1>Queue</h1>
  <p>Last published {published}. This is the copy the laptop uploaded. While <code>./scripts/run.sh</code> is running, a change locally shows up here. After that process stops, this page stays as it was.</p>
  {table}
</main>
</body>
</html>
"""


def render_queue_html(jobs: list[Job], *, published_at: datetime | None = None) -> str:
    moment = published_at or datetime.now(timezone.utc)
    stamp = moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    ordered = sorted(jobs, key=lambda job: job.created_at, reverse=True)
    if not ordered:
        table = "<p>No jobs yet.</p>"
    else:
        rows = "\n".join(_row(job) for job in ordered)
        table = (
            "<table><thead><tr>"
            "<th>Job</th><th>Kind</th><th>Status</th><th>Updated</th><th>Detail</th>"
            f"</tr></thead><tbody>\n{rows}\n</tbody></table>"
        )
    return _PAGE.format(published=html.escape(stamp), table=table)


def _row(job: Job) -> str:
    updated = job.updated_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cells = (
        html.escape(job.id),
        html.escape(job.kind),
        html.escape(job.status.value),
        html.escape(updated),
        html.escape(job.detail),
    )
    body = "".join(f"<td>{cell}</td>" for cell in cells)
    return f"<tr>{body}</tr>"


class QueuePublisher:
    """Uploads the queue page and the heartbeat the website uses for its light."""

    def __init__(self, settings: Settings, client=None) -> None:
        self.settings = settings
        self._client = client
        self._last_body: str | None = None
        self._last_error: str | None = None

    def publish(self, jobs: list[Job]) -> str | None:
        body = render_queue_html(jobs)
        if body == self._last_body:
            return None
        try:
            self._put(
                self.settings.queue_object_key,
                body.encode("utf-8"),
                "text/html; charset=utf-8",
            )
        except (BotoCoreError, ClientError, RuntimeError, OSError) as exc:
            return self._note_error(exc, "Queue upload failed")
        self._last_body = body
        self._last_error = None
        print(
            f"Published s3 queue page {self.settings.queue_object_key} ({len(jobs)} jobs).",
            flush=True,
        )
        return None

    def publish_presence(self, *, active: bool, run_id: str) -> str | None:
        """Tell the site whether this process is up and can take text."""
        try:
            upload = self._intake_post(run_id) if active else None
        except (BotoCoreError, ClientError, RuntimeError, OSError) as exc:
            upload = None
            active = False
            self._note_error(exc, "Intake link failed")
        live = bool(active and upload)
        document = {
            "active": live,
            "queue_live": live,
            "accepts_input": live,
            "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ttl_seconds": int(self.settings.status_ttl_seconds),
            "run_id": run_id if live else "",
            "upload": upload,
            "queue": list(reversed(read_queue(self.settings.queue_text_file))),
        }
        try:
            self._put(
                self.settings.status_object_key,
                json.dumps(document).encode("utf-8"),
                "application/json",
            )
        except (BotoCoreError, ClientError, RuntimeError, OSError) as exc:
            return self._note_error(exc, "Status upload failed")
        self._last_error = None
        print(
            "Published helper status: " + ("active" if live else "down") + ".",
            flush=True,
        )
        return None

    def accept_uploads(self, run_id: str) -> int:
        """Pull text uploaded while this run is active into the local queue."""
        try:
            return self._accept_uploads(run_id)
        except (BotoCoreError, ClientError, RuntimeError, OSError) as exc:
            self._note_error(exc, "Intake read failed")
            return 0

    def _accept_uploads(self, run_id: str) -> int:
        prefix = _intake_prefix(run_id)
        client = self._s3()
        bucket = self._bucket()
        accepted = 0
        token = None
        while True:
            kwargs: dict = {"Bucket": bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = client.list_objects_v2(**kwargs)
            for item in page.get("Contents") or []:
                key = item["Key"]
                if not key.startswith(prefix) or key.endswith("/"):
                    continue
                raw = client.get_object(Bucket=bucket, Key=key)["Body"].read(
                    MAX_INTAKE_BYTES + 1
                )
                client.delete_object(Bucket=bucket, Key=key)
                if len(raw) > MAX_INTAKE_BYTES:
                    continue
                text = raw.decode("utf-8", errors="replace").strip()
                url = youtube_url(text)
                if url is None:
                    continue
                if not append_queue(self.settings.queue_text_file, url):
                    print(f"Already in the queue: {url}", flush=True)
                    continue
                enqueue(self.settings, "echo", url)
                accepted += 1
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
        if accepted:
            print(f"Accepted {accepted} note(s) from the website.", flush=True)
        return accepted

    def discard_uploads(self, run_id: str | None = None) -> None:
        """Drop site uploads. A stopped run does not accept them later."""
        try:
            self._discard_uploads(run_id)
        except (BotoCoreError, ClientError, RuntimeError, OSError) as exc:
            self._note_error(exc, "Intake cleanup failed")

    def _discard_uploads(self, run_id: str | None) -> None:
        prefix = _intake_prefix(run_id) if run_id else "intake/"
        client = self._s3()
        bucket = self._bucket()
        token = None
        while True:
            kwargs: dict = {"Bucket": bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = client.list_objects_v2(**kwargs)
            for item in page.get("Contents") or []:
                client.delete_object(Bucket=bucket, Key=item["Key"])
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")

    def _intake_post(self, run_id: str) -> dict[str, object]:
        prefix = _intake_prefix(run_id)
        signed = self._s3().generate_presigned_post(
            self._bucket(),
            prefix + "${filename}",
            Fields={"success_action_status": "204"},
            Conditions=[
                ["starts-with", "$key", prefix],
                ["content-length-range", 1, MAX_INTAKE_BYTES],
                {"success_action_status": "204"},
            ],
            ExpiresIn=60,
        )
        return {"url": signed["url"], "fields": signed["fields"]}

    def _put(self, key: str, body: bytes, content_type: str) -> None:
        self._s3().put_object(
            Bucket=self._bucket(),
            Key=key,
            Body=body,
            ContentType=content_type,
            CacheControl="no-cache, no-store, must-revalidate",
        )

    def _s3(self):
        if self._client is None:
            self._client = self._client_from_file()
        return self._client

    def _bucket(self) -> str:
        bucket = read_tf_assignment(self.settings.terraform_var_file, "s3_bucket_name")
        if not bucket:
            raise RuntimeError("s3_bucket_name is missing from the terraform var file.")
        return bucket

    def _note_error(self, exc: BaseException, label: str) -> str:
        message = _public_error(exc)
        if message != self._last_error:
            self._last_error = message
            print(f"{label}: {message}", flush=True)
        return message

    def _client_from_file(self):
        secrets = read_secrets(self.settings.secrets_file)
        region = (
            secrets.get("AWS_DEFAULT_REGION")
            or read_tf_assignment(self.settings.terraform_var_file, "aws_region")
            or "us-east-1"
        )
        access = secrets.get("AWS_ACCESS_KEY_ID", "")
        secret = secrets.get("AWS_SECRET_ACCESS_KEY", "")
        token = secrets.get("AWS_SESSION_TOKEN", "")
        profile = secrets.get("AWS_PROFILE", "")
        if access and secret:
            return boto3.client(
                "s3",
                region_name=region,
                aws_access_key_id=access,
                aws_secret_access_key=secret,
                aws_session_token=token or None,
                config=_S3_CONFIG,
            )
        if profile:
            session = boto3.Session(profile_name=profile, region_name=region)
            return session.client("s3", config=_S3_CONFIG)
        raise RuntimeError(
            "AWS credentials are missing from config/secrets/secrets.env."
        )


def _public_error(exc: BaseException) -> str:
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "ClientError")
        return f"S3 {code}"
    return str(exc).splitlines()[0][:240]
