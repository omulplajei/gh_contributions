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


def test_team_share_happy_path() -> None:
    out = _load("team_share")
    share = out["repos"]["acme/api"]["team_share"]

    assert set(share) == {"commits", "pr", "comments"}

    assert share["commits"] == {
        "team":  {"commits": 4},
        "total": {"commits": 10},
        "share": pytest.approx(4 / 10),
    }

    assert share["pr"]["team"] == {
        "pull_requests_opened": 1,
        "pull_requests_merged": 1,
        "APPROVED":             2,
        "CHANGES_REQUESTED":    0,
        "COMMENTED":            0,
    }
    assert share["pr"]["total"] == {
        "pull_requests_opened": 2,
        "pull_requests_merged": 2,
        "APPROVED":             3,
        "CHANGES_REQUESTED":    0,
        "COMMENTED":            0,
    }
    assert share["pr"]["share"] == pytest.approx((1 + 1 + 2) / (2 + 2 + 3))

    assert share["comments"]["team"] == {
        "review_comments":          1,
        "pr_conversation_comments": 1,
        "issue_comments":           1,
    }
    assert share["comments"]["total"] == {
        "review_comments":          2,
        "pr_conversation_comments": 1,
        "issue_comments":           1,
    }
    assert share["comments"]["share"] == pytest.approx((1 + 1 + 1) / (2 + 1 + 1))


def test_team_share_zero_denominator_is_null(tmp_path) -> None:
    from gh_contributions.config import Config
    from datetime import date

    cfg = Config(
        usernames=["alice"],
        repos=["acme/api"],
        since=date(2026, 1, 1),
        until=date(2026, 6, 30),
        metrics=["team_share"],
    )
    repo_dir = tmp_path / "acme__api"
    repo_dir.mkdir()
    (repo_dir / "_meta.json").write_text("{}")
    for f in ("commits.json", "prs_by_created.json", "prs_by_merged.json",
              "prs_updated.json", "review_comments.json", "issue_comments.json"):
        (repo_dir / f).write_text("[]")
    (repo_dir / "reviews").mkdir()

    out = compute(tmp_path, cfg)
    share = out["repos"]["acme/api"]["team_share"]

    assert share["commits"] == {
        "team":  {"commits": 0},
        "total": {"commits": 0},
        "share": None,
    }
    assert share["pr"]["share"] is None
    assert share["pr"]["team"]  == {"pull_requests_opened": 0, "pull_requests_merged": 0,
                                     "APPROVED": 0, "CHANGES_REQUESTED": 0, "COMMENTED": 0}
    assert share["pr"]["total"] == {"pull_requests_opened": 0, "pull_requests_merged": 0,
                                     "APPROVED": 0, "CHANGES_REQUESTED": 0, "COMMENTED": 0}
    assert share["comments"]["share"] is None
    assert share["comments"]["team"]  == {"review_comments": 0, "pr_conversation_comments": 0, "issue_comments": 0}
    assert share["comments"]["total"] == {"review_comments": 0, "pr_conversation_comments": 0, "issue_comments": 0}


def test_team_share_pr_reviews_windowed(tmp_path) -> None:
    from gh_contributions.config import Config
    from datetime import date
    import json as _json

    cfg = Config(
        usernames=["alice"],
        repos=["acme/api"],
        since=date(2026, 1, 1),
        until=date(2026, 6, 30),
        metrics=["team_share"],
    )
    repo_dir = tmp_path / "acme__api"
    repo_dir.mkdir()
    (repo_dir / "_meta.json").write_text("{}")
    for f in ("commits.json", "prs_by_created.json", "prs_by_merged.json",
              "prs_updated.json", "review_comments.json", "issue_comments.json"):
        (repo_dir / f).write_text("[]")
    (repo_dir / "reviews").mkdir()
    # Two reviews: one in-window (counted), one before window (excluded from BOTH team and total).
    (repo_dir / "reviews" / "1.json").write_text(_json.dumps([
        {"user": {"login": "alice"}, "state": "APPROVED", "submitted_at": "2026-02-10T10:00:00Z"},
        {"user": {"login": "eve"},   "state": "APPROVED", "submitted_at": "2025-12-31T10:00:00Z"},
    ]))

    share = compute(tmp_path, cfg)["repos"]["acme/api"]["team_share"]
    assert share["pr"]["team"]["APPROVED"]  == 1
    assert share["pr"]["total"]["APPROVED"] == 1


def test_repo_error_propagates() -> None:
    out = _load("empty_repo")
    repo = out["repos"]["acme/api"]
    assert repo["error"] == "not_found"
    assert repo["per_user"] is None
    assert repo["team_share"] is None
    assert repo["truncated"] is None


def test_truncation_flag_propagates() -> None:
    out = _load("truncated")
    repo = out["repos"]["acme/api"]
    assert repo["truncated"].get("commits") is True


def test_layer_selection_authoring_only_omits_team_share() -> None:
    out = _load("authoring")  # config enables only "authoring"
    repo = out["repos"]["acme/api"]
    assert repo["team_share"] is None


def test_layer_selection_team_share_only_omits_per_user_details() -> None:
    out = _load("team_share")  # config enables only "team_share"
    per_user = out["repos"]["acme/api"]["per_user"]
    # Users appear as keys but with no authoring/collaboration blocks.
    assert "alice" in per_user
    assert per_user["alice"] == {}


def test_team_share_only_layer_propagates_truncation() -> None:
    out = _load("team_share")
    repo = out["repos"]["acme/api"]
    # With only team_share enabled, truncated must still propagate.
    assert repo["truncated"].get("commits") is True
