from datetime import date

import json
from pathlib import Path

from gh_contributions.fetch import _is_bucket_complete, _month_bounds, _months_between


def test_months_between_single_month() -> None:
    assert _months_between(date(2026, 5, 15), date(2026, 5, 20)) == ["2026-05"]


def test_months_between_spans_year_boundary() -> None:
    assert _months_between(date(2025, 11, 30), date(2026, 2, 1)) == [
        "2025-11", "2025-12", "2026-01", "2026-02",
    ]


def test_months_between_since_after_today_returns_empty() -> None:
    assert _months_between(date(2026, 8, 1), date(2026, 7, 2)) == []


def test_months_between_since_first_day_of_month() -> None:
    assert _months_between(date(2026, 5, 1), date(2026, 6, 1)) == ["2026-05", "2026-06"]


def test_month_bounds_past_month() -> None:
    lo, hi = _month_bounds("2026-05", today=date(2026, 7, 2))
    assert lo == date(2026, 5, 1)
    assert hi == date(2026, 5, 31)


def test_month_bounds_current_month_clamps_to_today() -> None:
    lo, hi = _month_bounds("2026-07", today=date(2026, 7, 2))
    assert lo == date(2026, 7, 1)
    assert hi == date(2026, 7, 2)


def test_month_bounds_february_leap_year() -> None:
    lo, hi = _month_bounds("2028-02", today=date(2028, 6, 1))
    assert lo == date(2028, 2, 1)
    assert hi == date(2028, 2, 29)


def test_month_bounds_february_non_leap_year() -> None:
    lo, hi = _month_bounds("2026-02", today=date(2026, 6, 1))
    assert lo == date(2026, 2, 1)
    assert hi == date(2026, 2, 28)


def test_is_bucket_complete_missing_dir(tmp_path: Path) -> None:
    assert _is_bucket_complete(tmp_path / "nope") is False


def test_is_bucket_complete_missing_meta(tmp_path: Path) -> None:
    (tmp_path / "commits.json").write_text("[]")
    assert _is_bucket_complete(tmp_path) is False


def test_is_bucket_complete_valid_meta(tmp_path: Path) -> None:
    (tmp_path / "_meta.json").write_text(json.dumps({
        "commits": {"total_count": 0, "truncated": False},
    }))
    assert _is_bucket_complete(tmp_path) is True


def test_is_bucket_complete_error_meta(tmp_path: Path) -> None:
    (tmp_path / "_meta.json").write_text(json.dumps({"error": "not_found"}))
    assert _is_bucket_complete(tmp_path) is False


def test_is_bucket_complete_malformed_meta(tmp_path: Path) -> None:
    (tmp_path / "_meta.json").write_text("{not json")
    assert _is_bucket_complete(tmp_path) is False


def test_effective_end_returns_last_day_of_previous_month() -> None:
    from gh_contributions.fetch import _effective_end
    assert _effective_end(date(2026, 7, 15)) == date(2026, 6, 30)
    assert _effective_end(date(2026, 7, 1))  == date(2026, 6, 30)
    assert _effective_end(date(2026, 7, 31)) == date(2026, 6, 30)


def test_effective_end_january_rolls_to_previous_december() -> None:
    from gh_contributions.fetch import _effective_end
    assert _effective_end(date(2026, 1, 1))  == date(2025, 12, 31)
    assert _effective_end(date(2026, 1, 15)) == date(2025, 12, 31)


def test_effective_end_march_after_leap_february() -> None:
    from gh_contributions.fetch import _effective_end
    assert _effective_end(date(2024, 3, 5)) == date(2024, 2, 29)


def test_effective_end_march_after_non_leap_february() -> None:
    from gh_contributions.fetch import _effective_end
    assert _effective_end(date(2026, 3, 5)) == date(2026, 2, 28)


from datetime import datetime, timezone
from unittest.mock import MagicMock

from gh_contributions.config import Config


def _write_complete_bucket(bucket: Path) -> None:
    bucket.mkdir(parents=True)
    (bucket / "_meta.json").write_text(json.dumps({
        "commits":          {"total_count": 0, "truncated": False},
        "prs_by_created":   {"total_count": 0, "truncated": False},
        "prs_by_merged":    {"total_count": 0, "truncated": False},
        "issues_by_created":{"total_count": 0, "truncated": False},
        "prs_updated":      {"total_count": 0, "truncated": False},
        "reviews":          {"total_count": 0, "truncated": False},
        "review_comments":  {"total_count": 0, "truncated": False},
        "issue_comments":   {"total_count": 0, "truncated": False},
    }))
    for f in ("commits.json", "prs_by_created.json", "prs_by_merged.json",
              "issues_by_created.json", "prs_updated.json",
              "review_comments.json", "issue_comments.json"):
        (bucket / f).write_text("[]")
    (bucket / "reviews").mkdir()


def test_run_skips_complete_buckets_and_fetches_missing(tmp_path, monkeypatch) -> None:
    from gh_contributions import run as run_mod

    fake_today = date(2026, 7, 2)

    class _FakeDT:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 7, 2, 12, 0, 0, tzinfo=tz or timezone.utc)

    monkeypatch.setattr(run_mod, "datetime", _FakeDT)
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.chdir(tmp_path)

    _write_complete_bucket(tmp_path / "out" / "raw" / "2026-05" / "acme__api")
    _write_complete_bucket(tmp_path / "out" / "raw" / "2026-06" / "acme__api")

    (tmp_path / "config.yml").write_text(
        "usernames: [alice]\nrepos: [acme/api]\nsince: 2026-05-01\nmetrics: [authoring]\n"
    )

    fetch_calls: list[tuple] = []

    def fake_fetch_repo(client, repo, since, until, out_dir):
        fetch_calls.append((repo, since, until, Path(out_dir).name))
        bucket = Path(out_dir) / repo.replace("/", "__")
        _write_complete_bucket(bucket)

    monkeypatch.setattr(run_mod, "fetch_repo", fake_fetch_repo)
    monkeypatch.setattr(run_mod, "GitHubClient", lambda token: MagicMock())

    rc = run_mod.main([])
    assert rc == 0
    assert fetch_calls == [("acme/api", date(2026, 7, 1), fake_today, "2026-07")]

    run_dirs = [p for p in (tmp_path / "out").iterdir() if p.name != "raw"]
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "metrics.json").exists()


def test_run_missing_token_returns_2(tmp_path, monkeypatch) -> None:
    from gh_contributions import run as run_mod

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yml").write_text(
        "usernames: [alice]\nrepos: [acme/api]\nsince: 2026-05-01\nmetrics: [authoring]\n"
    )
    assert run_mod.main([]) == 2
