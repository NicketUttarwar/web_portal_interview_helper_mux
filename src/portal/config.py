"""Paths and files the bridge reads. Secrets stay in the returned dict."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_PAYLOAD_CHARS = 100_000


@dataclass(frozen=True)
class Settings:
    root: Path
    assets_dir: Path
    job_dir_pattern: str
    product_dir: Path
    secrets_file: Path
    terraform_var_file: Path
    publish_interval_seconds: float
    queue_object_key: str
    queue_state_file: Path
    inbox_dir: Path
    job_timeout_seconds: float
    status_object_key: str = "status.json"
    status_interval_seconds: float = 5
    status_ttl_seconds: float = 12
    queue_text_file: Path = Path("queue.txt")
    title_lookup_timeout_seconds: float = 30

    def job_dir(self, job_id: str) -> Path:
        relative = self.job_dir_pattern.format(job_id=job_id)
        path = Path(relative)
        if path.is_absolute():
            return path
        return self.root / path


def load_settings(root: Path | None = None) -> Settings:
    repo = (root or REPO_ROOT).resolve()
    defaults_path = repo / "config" / "app.defaults.json"
    defaults = json.loads(defaults_path.read_text(encoding="utf-8"))
    assets_dir = _under_root(repo, str(defaults["assets_dir"]))
    return Settings(
        root=repo,
        assets_dir=assets_dir,
        job_dir_pattern=str(defaults["job_dir_pattern"]),
        product_dir=_resolve_product_dir(repo, str(defaults["product_dir"])),
        secrets_file=_under_root(repo, str(defaults["secrets_file"])),
        terraform_var_file=_under_root(repo, str(defaults["terraform_var_file"])),
        publish_interval_seconds=float(defaults.get("publish_interval_seconds", 15)),
        queue_object_key=str(defaults.get("queue_object_key", "queue.html")),
        queue_state_file=_under_root(
            repo, str(defaults.get("queue_state_file", "ASSETS/queue.json"))
        ),
        inbox_dir=_under_root(repo, str(defaults.get("inbox_dir", "ASSETS/inbox"))),
        job_timeout_seconds=float(defaults.get("job_timeout_seconds", 60)),
        status_object_key=str(defaults.get("status_object_key", "status.json")),
        status_interval_seconds=float(defaults.get("status_interval_seconds", 5)),
        status_ttl_seconds=float(defaults.get("status_ttl_seconds", 12)),
        queue_text_file=_under_root(repo, str(defaults.get("queue_text_file", "queue.txt"))),
        title_lookup_timeout_seconds=float(defaults.get("title_lookup_timeout_seconds", 30)),
    )


def read_secrets(path: Path) -> dict[str, str]:
    """Read KEY=VALUE lines. Callers must not copy this onto a job process."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def read_tf_assignment(path: Path, name: str) -> str:
    if not path.is_file():
        return ""
    prefix = f"{name}"
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line.startswith(prefix):
            continue
        left, sep, right = line.partition("=")
        if sep != "=" or left.strip() != name:
            continue
        return right.strip().strip('"').strip("'")
    return ""


def _under_root(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path


def _resolve_product_dir(root: Path, name: str) -> Path:
    inside = _under_root(root, name)
    if inside.is_dir():
        return inside
    sibling = root.parent / name
    return sibling
