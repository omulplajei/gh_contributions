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
