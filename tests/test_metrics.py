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
