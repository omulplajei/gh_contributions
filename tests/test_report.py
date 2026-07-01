"""Tests for gh_contributions.report."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gh_contributions.report import _aggregate, render, main


def _repo(commits_by_user=None, ts=None, truncated=None, error=None):
    """Build a minimal per-repo entry matching metrics.py output shape."""
    per_user = None if error else {}
    if commits_by_user:
        for login, counts in commits_by_user.items():
            per_user[login] = {
                "authoring": {
                    "commits": counts.get("commits", 0),
                    "pull_requests_opened": counts.get("pull_requests_opened", 0),
                    "pull_requests_merged": counts.get("pull_requests_merged", 0),
                    "issues_opened": counts.get("issues_opened", 0),
                },
                "collaboration": {
                    "reviews_given": {
                        "APPROVED":          counts.get("APPROVED", 0),
                        "CHANGES_REQUESTED": counts.get("CHANGES_REQUESTED", 0),
                        "COMMENTED":         counts.get("COMMENTED", 0),
                    },
                    "review_comments":          counts.get("review_comments", 0),
                    "pr_conversation_comments": counts.get("pr_conversation_comments", 0),
                    "issue_comments":           counts.get("issue_comments", 0),
                    "cross_team_reviews":       counts.get("cross_team_reviews", 0),
                },
            }
    return {
        "per_user": per_user,
        "team_share": ts,
        "truncated": None if error else (truncated or {}),
        "error": error,
    }


def _ts(commits=(0, 0), prs=(0, 0), reviews=(0, 0), comments=(0, 0)):
    def bucket(pair):
        team, total = pair
        return {"team": team, "total": total, "share": (team / total) if total else None}
    return {
        "commits":              bucket(commits),
        "pull_requests_opened": bucket(prs),
        "reviews_given":        bucket(reviews),
        "comments":             bucket(comments),
    }


def _metrics(repos, layers=("authoring", "collaboration", "team_share")):
    return {
        "run": {
            "since": "2026-01-01",
            "until": "2026-06-30",
            "generated_at": "2026-07-01T20:00:00Z",
            "metrics_layers": list(layers),
        },
        "repos": repos,
    }


# ---------- _aggregate ----------


def test_aggregate_sums_per_user_across_repos() -> None:
    metrics = _metrics({
        "acme/api": _repo(
            commits_by_user={
                "alice": {"commits": 5, "pull_requests_opened": 2, "APPROVED": 3, "review_comments": 4},
                "bob":   {"commits": 2, "issues_opened": 1},
            },
            ts=_ts(commits=(7, 10)),
        ),
        "acme/web": _repo(
            commits_by_user={
                "alice": {"commits": 3, "pull_requests_opened": 1, "APPROVED": 1},
            },
            ts=_ts(commits=(3, 5)),
        ),
    })
    agg = _aggregate(metrics)
    assert agg["per_user"]["alice"]["authoring"] == {
        "commits": 8, "pull_requests_opened": 3, "pull_requests_merged": 0, "issues_opened": 0,
    }
    assert agg["per_user"]["bob"]["authoring"] == {
        "commits": 2, "pull_requests_opened": 0, "pull_requests_merged": 0, "issues_opened": 1,
    }
    assert agg["per_user"]["alice"]["collaboration"]["reviews_given"] == {
        "APPROVED": 4, "CHANGES_REQUESTED": 0, "COMMENTED": 0,
    }
    assert agg["per_user"]["alice"]["collaboration"]["review_comments"] == 4


def test_aggregate_recomputes_team_share_ratios() -> None:
    metrics = _metrics({
        "acme/api": _repo(ts=_ts(commits=(7, 10), reviews=(0, 0))),
        "acme/web": _repo(ts=_ts(commits=(3, 5), reviews=(0, 0))),
    })
    agg = _aggregate(metrics)
    assert agg["team_share"]["commits"] == {"team": 10, "total": 15, "share": pytest.approx(10 / 15)}
    # 0/0 + 0/0 aggregates to team=0, total=0, share=None (not division-by-zero).
    assert agg["team_share"]["reviews_given"] == {"team": 0, "total": 0, "share": None}


def test_aggregate_unions_truncation_flags() -> None:
    metrics = _metrics({
        "acme/api": _repo(ts=_ts(), truncated={}),
        "acme/web": _repo(ts=_ts(), truncated={"commits": True}),
    })
    agg = _aggregate(metrics)
    assert agg["truncated"] == {"commits": True}


def test_aggregate_returns_none_if_all_repos_errored() -> None:
    metrics = _metrics({
        "acme/api": _repo(error="not_found"),
    })
    assert _aggregate(metrics) is None


def test_aggregate_skips_errored_repos_but_uses_healthy_ones() -> None:
    metrics = _metrics({
        "broken":   _repo(error="not_found"),
        "acme/web": _repo(
            commits_by_user={"alice": {"commits": 4}},
            ts=_ts(commits=(4, 8)),
        ),
    })
    agg = _aggregate(metrics)
    assert agg is not None
    assert agg["per_user"]["alice"]["authoring"]["commits"] == 4
    assert agg["team_share"]["commits"] == {"team": 4, "total": 8, "share": 0.5}
