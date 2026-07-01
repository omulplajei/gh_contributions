from pathlib import Path

import pytest

from gh_contributions.config import load_config
from gh_contributions.metrics import compute


FIXTURES = Path(__file__).parent / "fixtures"


def _load(fixture: str):
    cfg = load_config(str(FIXTURES / fixture / "config.yml"))
    return compute(FIXTURES / fixture / "raw", cfg)


def test_authoring_counts_per_user() -> None:
    out = _load("authoring")
    repo = out["repos"]["acme/api"]
    assert repo["error"] is None
    users = repo["per_user"]
    # Only team users are keys; eve and dependabot[bot] are absent.
    assert set(users) == {"alice", "bob"}
    assert users["alice"]["authoring"] == {
        "commits": 2,
        "pull_requests_opened": 2,
        "pull_requests_merged": 1,
        "issues_opened": 1,
    }
    assert users["bob"]["authoring"] == {
        "commits": 1,
        "pull_requests_opened": 1,
        "pull_requests_merged": 1,
        "issues_opened": 0,
    }


def test_authoring_only_layer_no_team_share_no_collab() -> None:
    out = _load("authoring")
    repo = out["repos"]["acme/api"]
    assert repo["team_share"] is None
    # Users don't have a collaboration block when only authoring is enabled.
    for u in repo["per_user"].values():
        assert "collaboration" not in u


def test_run_metadata_present() -> None:
    out = _load("authoring")
    assert out["run"]["since"] == "2026-01-01"
    assert out["run"]["until"] == "2026-06-30"
    assert out["run"]["metrics_layers"] == ["authoring"]
    assert "generated_at" in out["run"]


def test_collaboration_reviews_by_state() -> None:
    out = _load("collaboration")
    users = out["repos"]["acme/api"]["per_user"]
    # PENDING and DISMISSED are ignored.
    assert users["alice"]["collaboration"]["reviews_given"] == {
        "APPROVED": 2, "CHANGES_REQUESTED": 0, "COMMENTED": 1,
    }
    assert users["bob"]["collaboration"]["reviews_given"] == {
        "APPROVED": 1, "CHANGES_REQUESTED": 1, "COMMENTED": 0,
    }


def test_collaboration_cross_team_reviews() -> None:
    out = _load("collaboration")
    users = out["repos"]["acme/api"]["per_user"]
    # PR 2 is authored by eve (not in team). alice reviewed PR 2 twice (COMMENTED, APPROVED).
    # PR 1 authored by alice (team), so bob's review on PR 1 is not cross-team.
    # PR 3 authored by bob (team), no cross-team review there.
    assert users["alice"]["collaboration"]["cross_team_reviews"] == 2
    assert users["bob"]["collaboration"]["cross_team_reviews"] == 0


def test_collaboration_review_comments_windowed() -> None:
    out = _load("collaboration")
    users = out["repos"]["acme/api"]["per_user"]
    # alice has 3 total; 1 is before window (2025-12-31) -> 2 counted. eve excluded (not team).
    assert users["alice"]["collaboration"]["review_comments"] == 2
    assert users["bob"]["collaboration"]["review_comments"] == 1


def test_collaboration_pr_vs_issue_comment_split() -> None:
    out = _load("collaboration")
    users = out["repos"]["acme/api"]["per_user"]
    # alice: both comments are on PR #1 and #2 -> pr_conversation_comments=2, issue_comments=0
    # bob: comments on #50 and #51 which are not in prs_updated -> issue_comments=2
    assert users["alice"]["collaboration"]["pr_conversation_comments"] == 2
    assert users["alice"]["collaboration"]["issue_comments"] == 0
    assert users["bob"]["collaboration"]["pr_conversation_comments"] == 0
    assert users["bob"]["collaboration"]["issue_comments"] == 2
