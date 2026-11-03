from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from portal.allowlist import job_env
from portal.config import REPO_ROOT, Settings, read_secrets, read_tf_assignment
from portal.jobs import run_one
from portal.models import Job, JobStatus
from portal.publish import QueuePublisher, render_queue_html
from portal.store import acquire_bridge_lock, enqueue, list_visible


def settings(tmp_path) -> Settings:
    return Settings(
        root=REPO_ROOT,
        assets_dir=tmp_path / "ASSETS",
        job_dir_pattern=str(tmp_path / "jobs" / "{job_id}"),
        product_dir=tmp_path / "interview_helper_mux",
        secrets_file=tmp_path / "secrets.env",
        terraform_var_file=tmp_path / "terraform.tfvars",
        publish_interval_seconds=15,
        queue_object_key="queue.html",
        queue_state_file=tmp_path / "ASSETS" / "queue.json",
        inbox_dir=tmp_path / "ASSETS" / "inbox",
        job_timeout_seconds=10,
        queue_text_file=tmp_path / "queue.txt",
    )


def test_echo_writes_the_payload_and_finishes(tmp_path):
    cfg = settings(tmp_path)
    enqueue(cfg, "echo", "hello tape")
    job = run_one(cfg)
    assert job is not None
    assert job.status is JobStatus.finished
    written = (cfg.job_dir(job.id) / "payload.txt").read_text(encoding="utf-8")
    assert written == "hello tape"


def test_unknown_kind_is_rejected_and_not_started(tmp_path):
    cfg = settings(tmp_path)
    enqueue(cfg, "shell", "echo hello")
    job = run_one(cfg)
    assert job is not None
    assert job.status is JobStatus.rejected
    assert not cfg.job_dir(job.id).exists()


def test_public_page_omits_the_payload_and_escapes_detail():
    job = Job(
        id="job-1",
        kind="echo",
        status=JobStatus.finished,
        payload="SECRET-PAYLOAD-XYZ",
        detail="<script>alert(1)</script>",
        created_at=datetime(2026, 11, 3, tzinfo=timezone.utc),
        updated_at=datetime(2026, 11, 3, 1, tzinfo=timezone.utc),
    )
    page = render_queue_html([job], published_at=datetime(2026, 11, 3, 2, tzinfo=timezone.utc))
    assert "SECRET-PAYLOAD-XYZ" not in page
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "job-1" in page


def test_publish_puts_the_queue_page(tmp_path):
    cfg = settings(tmp_path)
    cfg.terraform_var_file.write_text(
        's3_bucket_name = "zero-shot-media-website-bucket"\n',
        encoding="utf-8",
    )

    class FakeS3:
        def __init__(self) -> None:
            self.calls = []

        def put_object(self, **kwargs):
            self.calls.append(kwargs)

    client = FakeS3()
    published = QueuePublisher(cfg, client=client).publish([])
    assert published is None
    call = client.calls[0]
    assert call["Bucket"] == "zero-shot-media-website-bucket"
    assert call["Key"] == "queue.html"
    assert call["ContentType"] == "text/html; charset=utf-8"
    assert b"No jobs yet." in call["Body"]


def test_job_env_drops_aws_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "super-secret")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA-test")
    env = job_env(settings(tmp_path))
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "AWS_ACCESS_KEY_ID" not in env
    assert env["PYTHONPATH"].endswith("/src")


def test_a_second_bridge_refuses_the_lock(tmp_path):
    cfg = settings(tmp_path)
    held = acquire_bridge_lock(cfg)
    try:
        with pytest.raises(SystemExit):
            acquire_bridge_lock(cfg)
    finally:
        held.close()


class _Body:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self, _n: int = -1) -> bytes:
        return self.data


class _FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, **kwargs):
        self.objects[kwargs["Key"]] = kwargs["Body"]

    def generate_presigned_post(self, _bucket, key, Fields=None, Conditions=None, ExpiresIn=None):
        return {"url": "https://bucket.s3.amazonaws.com/", "fields": {"key": key, "policy": "p"}}

    def list_objects_v2(self, **kwargs):
        prefix = kwargs["Prefix"]
        contents = [{"Key": key} for key in sorted(self.objects) if key.startswith(prefix)]
        return {"Contents": contents, "IsTruncated": False}

    def get_object(self, **kwargs):
        return {"Body": _Body(self.objects[kwargs["Key"]])}

    def delete_object(self, **kwargs):
        self.objects.pop(kwargs["Key"], None)


def _publisher(tmp_path):
    cfg = settings(tmp_path)
    cfg.terraform_var_file.write_text(
        's3_bucket_name = "zero-shot-media-website-bucket"\n',
        encoding="utf-8",
    )
    return cfg, QueuePublisher(cfg, client=_FakeS3())


def test_light_is_off_when_the_helper_is_down(tmp_path):
    _cfg, publisher = _publisher(tmp_path)
    assert publisher.publish_presence(active=False, run_id="run-1") is None
    document = json.loads(publisher._client.objects["status.json"])
    assert document["active"] is False
    assert document["queue_live"] is False
    assert document["accepts_input"] is False
    assert document["upload"] is None


def test_light_is_on_only_with_a_fresh_intake_link(tmp_path):
    _cfg, publisher = _publisher(tmp_path)
    assert publisher.publish_presence(active=True, run_id="abc") is None
    document = json.loads(publisher._client.objects["status.json"])
    assert document["active"] is True
    assert document["queue_live"] is True
    assert document["accepts_input"] is True
    assert document["upload"]["fields"]["key"].startswith("intake/abc/")


def test_site_text_is_accepted_only_for_the_live_run(tmp_path):
    cfg, publisher = _publisher(tmp_path)
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    publisher._client.objects["intake/abc/note.txt"] = url.encode()
    publisher._client.objects["intake/abc/nope.txt"] = b"not a youtube url"
    publisher._client.objects["intake/old/note.txt"] = b"left after shutdown"
    publisher.discard_uploads("old")
    assert publisher.accept_uploads("abc") == 1
    assert cfg.queue_text_file.read_text(encoding="utf-8").splitlines() == [url]
    jobs = list_visible(cfg)
    assert jobs[0].payload == url
    assert "intake/abc/note.txt" not in publisher._client.objects
    assert "intake/abc/nope.txt" not in publisher._client.objects
    assert "intake/old/note.txt" not in publisher._client.objects
    page = render_queue_html(jobs)
    assert url not in page


def test_the_same_video_is_not_added_twice(tmp_path):
    cfg, publisher = _publisher(tmp_path)
    watch = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    publisher._client.objects["intake/abc/a.txt"] = watch.encode()
    publisher._client.objects["intake/abc/b.txt"] = b"https://youtu.be/dQw4w9WgXcQ?si=tracking"
    assert publisher.accept_uploads("abc") == 1
    assert cfg.queue_text_file.read_text(encoding="utf-8").splitlines() == [watch]


def test_reads_assignments_without_exporting_them(tmp_path):
    secrets = tmp_path / "secrets.env"
    secrets.write_text(
        "AWS_ACCESS_KEY_ID=abc\nAWS_SECRET_ACCESS_KEY=def\n# comment\n\n",
        encoding="utf-8",
    )
    tfvars = tmp_path / "terraform.tfvars"
    tfvars.write_text(
        'aws_region = "us-east-1"\ns3_bucket_name = "bucket-a"\n',
        encoding="utf-8",
    )
    assert read_secrets(secrets)["AWS_SECRET_ACCESS_KEY"] == "def"
    assert read_tf_assignment(tfvars, "s3_bucket_name") == "bucket-a"
    assert read_tf_assignment(tfvars, "aws_region") == "us-east-1"
