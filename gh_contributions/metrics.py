"""Pure computation of team-activity metrics from on-disk raw pages."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config


def compute(raw_dir: Path, config: Config) -> dict:
    result: dict[str, Any] = {
        "run": {
            "since": config.since.isoformat(),
            "until": config.until.isoformat(),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "metrics_layers": list(config.metrics),
        },
        "repos": {},
    }
    for repo in config.repos:
        result["repos"][repo] = _compute_repo(raw_dir, repo, config)
    return result


def _compute_repo(raw_dir: Path, repo: str, config: Config) -> dict:
    owner, name = repo.split("/", 1)
    repo_dir = raw_dir / f"{owner}__{name}"

    meta = _read_json(repo_dir / "_meta.json", default={})
    if isinstance(meta, dict) and meta.get("error"):
        return {
            "per_user": None,
            "team_share": None,
            "truncated": None,
            "error": meta["error"],
        }

    per_user: dict[str, dict] = {u: {} for u in config.usernames}
    truncated: dict[str, bool] = {}

    if "authoring" in config.metrics:
        _apply_authoring(repo_dir, config, per_user, truncated)

    return {
        "per_user": per_user,
        "team_share": None,
        "truncated": truncated,
        "error": None,
    }


def _apply_authoring(
    repo_dir: Path,
    config: Config,
    per_user: dict[str, dict],
    truncated: dict[str, bool],
) -> None:
    team = set(config.usernames)
    counts = {u: {
        "commits": 0,
        "pull_requests_opened": 0,
        "pull_requests_merged": 0,
        "issues_opened": 0,
    } for u in team}

    for src, key in [
        ("commits.json",           "commits"),
        ("prs_by_created.json",    "pull_requests_opened"),
        ("prs_by_merged.json",     "pull_requests_merged"),
        ("issues_by_created.json", "issues_opened"),
    ]:
        for item in _read_json(repo_dir / src, default=[]):
            login = _author_login(item, src)
            if login in team:
                counts[login][key] += 1

    for u in team:
        per_user[u]["authoring"] = counts[u]

    meta = _read_json(repo_dir / "_meta.json", default={})
    for src_key in ("commits", "prs_by_created", "prs_by_merged", "issues_by_created"):
        if isinstance(meta, dict) and meta.get(src_key, {}).get("truncated"):
            truncated[src_key] = True


def _author_login(item: dict, src: str) -> str | None:
    # commits.json uses top-level `author.login`; PR/issue search results use `user.login`.
    if src == "commits.json":
        author = item.get("author") or {}
        return author.get("login") if isinstance(author, dict) else None
    user = item.get("user") or {}
    return user.get("login") if isinstance(user, dict) else None


def _read_json(path: Path, *, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
