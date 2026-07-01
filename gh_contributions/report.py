"""Render a self-contained HTML report from metrics.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path


_AUTHORING_KEYS = ("commits", "pull_requests_opened", "pull_requests_merged", "issues_opened")
_COLLAB_INT_KEYS = ("review_comments", "pr_conversation_comments", "issue_comments", "cross_team_reviews")
_REVIEW_STATES = ("APPROVED", "CHANGES_REQUESTED", "COMMENTED")
_TEAM_SHARE_BUCKETS = ("commits", "pull_requests_opened", "reviews_given", "comments")

_ASSET_DIR = Path(__file__).parent / "assets"


def _aggregate(metrics: dict) -> dict | None:
    healthy = {r: v for r, v in metrics.get("repos", {}).items() if not v.get("error")}
    if not healthy:
        return None

    per_user: dict[str, dict] = {}
    for repo_v in healthy.values():
        for login, layers in (repo_v.get("per_user") or {}).items():
            slot = per_user.setdefault(login, {})
            if "authoring" in layers:
                auth = slot.setdefault("authoring", {k: 0 for k in _AUTHORING_KEYS})
                for k in _AUTHORING_KEYS:
                    auth[k] += layers["authoring"].get(k, 0)
            if "collaboration" in layers:
                collab = slot.setdefault("collaboration", {
                    "reviews_given": {s: 0 for s in _REVIEW_STATES},
                    **{k: 0 for k in _COLLAB_INT_KEYS},
                })
                for s in _REVIEW_STATES:
                    collab["reviews_given"][s] += layers["collaboration"]["reviews_given"].get(s, 0)
                for k in _COLLAB_INT_KEYS:
                    collab[k] += layers["collaboration"].get(k, 0)

    team_share = None
    ts_repos = [v.get("team_share") for v in healthy.values() if v.get("team_share")]
    if ts_repos:
        team_share = {}
        for bucket in _TEAM_SHARE_BUCKETS:
            t = sum(ts[bucket]["team"] for ts in ts_repos)
            n = sum(ts[bucket]["total"] for ts in ts_repos)
            team_share[bucket] = {"team": t, "total": n, "share": (t / n) if n else None}

    truncated: dict[str, bool] = {}
    for v in healthy.values():
        for k, flag in (v.get("truncated") or {}).items():
            if flag:
                truncated[k] = True

    return {"per_user": per_user, "team_share": team_share, "truncated": truncated, "error": None}


def render(metrics: dict) -> str:
    raise NotImplementedError("render implemented in Task 4")


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError("main implemented in Task 6")
