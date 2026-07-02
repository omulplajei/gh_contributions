# Monthly raw-data cache — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Partition raw GitHub API responses into per-month buckets under `out/raw/<YYYY-MM>/` so months already downloaded are reused across runs; drop `until` from `config.yml`.

**Architecture:** `run.py` enumerates months from `since` to today (UTC), skips buckets whose `_meta.json` shows completion, and reuses today's `fetch_repo` per (month, repo) with month-scoped date bounds. `metrics.py` walks the same month list per repo, merges the JSON blobs across months, and computes metrics with a single `[since, today]` window filter.

**Tech Stack:** Python 3.10+, `requests`, `PyYAML`, `pytest`. See [requirements.txt](requirements.txt).

## Global Constraints

- Date arithmetic uses UTC. "today" = `datetime.now(timezone.utc).date()`.
- `metrics.json` schema keys stay identical (`per_user`, `team_share`, `truncated`, `error`, `run.{since,until,generated_at,metrics_layers}`).
- Fetcher HTTP behavior is unchanged; only its callers change.
- Live GitHub calls are never made from tests; use a mocked `GitHubClient`.
- No new runtime dependencies.

---

## File Structure

Modified files:
- `gh_contributions/config.py` — remove `until` from `Config`, reject if present, keep `since` validation.
- `gh_contributions/fetch.py` — add `_months_between`, `_month_bounds`, `_is_bucket_complete`. `fetch_repo` signature is unchanged.
- `gh_contributions/run.py` — new month-outer / repo-inner loop; passes `today` into `compute()`.
- `gh_contributions/metrics.py` — new `today` parameter on `compute`, new `_load_endpoint` / `_load_reviews` helpers, refactored `_apply_*` to take pre-merged data, partial-error handling in `_compute_repo`.
- `README.md` — document monthly cache, `until` removal, refresh workflow.
- `config.yml` — drop `until`.
- `tests/test_config.py` — swap `until <  since` case for `until rejected` case; drop `until` from `VALID_YAML`.
- `tests/test_metrics.py` — pass `today` in `_load`; drop `until` from `Config(...)` calls; new tests for multi-month, missing-month, partial-error.
- `tests/fixtures/authoring/`, `collaboration/`, `team_share/`, `empty_repo/`, `truncated/` — migrate `raw/<owner>__<repo>/` to `raw/2026-02/<owner>__<repo>/`; drop `until` and set `since: 2026-02-01`.

Created files:
- `tests/test_fetch.py` — `_months_between`, `_month_bounds`, `_is_bucket_complete`, and run-loop skip-if-complete behavior with a mocked client.
- `tests/fixtures/multi_month/config.yml` and `raw/2026-05/`, `raw/2026-06/`.
- `tests/fixtures/missing_month/config.yml` and `raw/2026-05/`, `raw/2026-07/` (June absent).
- `tests/fixtures/partial_error/config.yml` and `raw/2026-05/`, `raw/2026-06/`, `raw/2026-07/` (July's `_meta.json` has an error).

---

## Task 1: Drop `until` from `Config` and reject it if present

**Files:**
- Modify: `gh_contributions/config.py` (Config dataclass, `load_config`)
- Modify: `tests/test_config.py`
- Modify: `config.yml` (repo root)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `Config(usernames: list[str], repos: list[str], since: date, metrics: list[str])` — no `until` field.
  - `load_config(path)` raises `ConfigError` with message `"'until' has been removed from config; the analysis window now ends at today (UTC). Please remove this key."` if `until` is present.

- [ ] **Step 1: Write the failing tests**

Replace the body of `tests/test_config.py` where noted.

Replace `VALID_YAML` (drop `until`):

```python
VALID_YAML = textwrap.dedent("""\
    usernames:
      - alice
      - bob
    repos:
      - acme/api
    since: 2026-01-01
    metrics:
      - authoring
      - collaboration
      - team_share
""")
```

Update `test_load_happy_path` (drop `until` arg):

```python
def test_load_happy_path(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, VALID_YAML))
    assert cfg == Config(
        usernames=["alice", "bob"],
        repos=["acme/api"],
        since=date(2026, 1, 1),
        metrics=["authoring", "collaboration", "team_share"],
    )
```

Replace `test_until_before_since_errors` with:

```python
def test_until_key_is_rejected(tmp_path: Path) -> None:
    body = VALID_YAML.replace(
        "since: 2026-01-01\n",
        "since: 2026-01-01\nuntil: 2026-06-30\n",
    )
    with pytest.raises(ConfigError, match="'until' has been removed"):
        load_config(_write(tmp_path, body))
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: `test_load_happy_path` fails on the `Config` field mismatch; `test_until_key_is_rejected` fails because `load_config` currently requires `until` rather than rejecting it.

- [ ] **Step 3: Update `Config` and `load_config`**

In `gh_contributions/config.py`:

Replace the `Config` dataclass:

```python
@dataclass(frozen=True)
class Config:
    usernames: list[str]
    repos: list[str]
    since: date
    metrics: list[str]
```

Replace the `since`/`until` block inside `load_config` (currently lines around 49-52) with:

```python
    if "until" in raw:
        raise ConfigError(
            "'until' has been removed from config; the analysis window now "
            "ends at today (UTC). Please remove this key."
        )

    since = _require_date(raw, "since")
```

Update the final `return Config(...)` to drop `until=until`:

```python
    return Config(
        usernames=usernames,
        repos=repos,
        since=since,
        metrics=metrics,
    )
```

- [ ] **Step 4: Update `config.yml` at the repo root**

Delete the `until: 2026-07-01` line.

- [ ] **Step 5: Run tests and verify they pass**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add gh_contributions/config.py tests/test_config.py config.yml
git commit -m "config: remove 'until' key; reject if present"
```

---

## Task 2: Add month enumeration helpers

**Files:**
- Modify: `gh_contributions/fetch.py` (append)
- Create: `tests/test_fetch.py`

**Interfaces:**
- Consumes: `datetime.date`.
- Produces:
  - `_months_between(since: date, today: date) -> list[str]` — returns month tokens `"YYYY-MM"` from the month containing `since` up to and including the month containing `today`. Returns `[]` if `since > today`.
  - `_month_bounds(month: str, today: date) -> tuple[date, date]` — returns `(first_day_of_month, last_day_of_month_or_today)`. If `month` is the same calendar month as `today`, the upper bound is `today`; otherwise it is the calendar last day of that month.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fetch.py`:

```python
from datetime import date

import pytest

from gh_contributions.fetch import _month_bounds, _months_between


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
    # 2028 is a leap year.
    lo, hi = _month_bounds("2028-02", today=date(2028, 6, 1))
    assert lo == date(2028, 2, 1)
    assert hi == date(2028, 2, 29)


def test_month_bounds_february_non_leap_year() -> None:
    lo, hi = _month_bounds("2026-02", today=date(2026, 6, 1))
    assert lo == date(2026, 2, 1)
    assert hi == date(2026, 2, 28)
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `python3 -m pytest tests/test_fetch.py -v`
Expected: import errors for `_months_between` and `_month_bounds`.

- [ ] **Step 3: Implement the helpers**

Append to `gh_contributions/fetch.py`:

```python
from calendar import monthrange


def _months_between(since: date, today: date) -> list[str]:
    if since > today:
        return []
    out: list[str] = []
    y, m = since.year, since.month
    end_y, end_m = today.year, today.month
    while (y, m) <= (end_y, end_m):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            m = 1
            y += 1
    return out


def _month_bounds(month: str, today: date) -> tuple[date, date]:
    year_s, mon_s = month.split("-", 1)
    year, mon = int(year_s), int(mon_s)
    first = date(year, mon, 1)
    if (year, mon) == (today.year, today.month):
        return first, today
    last_day = monthrange(year, mon)[1]
    return first, date(year, mon, last_day)
```

Also add the `from calendar import monthrange` at the top of the file if not already imported.

- [ ] **Step 4: Run tests and verify they pass**

Run: `python3 -m pytest tests/test_fetch.py -v`
Expected: all 8 pass.

- [ ] **Step 5: Commit**

```bash
git add gh_contributions/fetch.py tests/test_fetch.py
git commit -m "fetch: add month enumeration and month-bounds helpers"
```

---

## Task 3: Add bucket-completion helper

**Files:**
- Modify: `gh_contributions/fetch.py` (append)
- Modify: `tests/test_fetch.py` (append)

**Interfaces:**
- Consumes: `pathlib.Path`.
- Produces: `_is_bucket_complete(bucket_dir: Path) -> bool` — returns `True` iff `bucket_dir / "_meta.json"` exists, parses as JSON, and its top level does not contain an `"error"` key.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fetch.py`:

```python
import json
from pathlib import Path

from gh_contributions.fetch import _is_bucket_complete


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
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `python3 -m pytest tests/test_fetch.py -v`
Expected: import error for `_is_bucket_complete`.

- [ ] **Step 3: Implement the helper**

Append to `gh_contributions/fetch.py`:

```python
def _is_bucket_complete(bucket_dir: Path) -> bool:
    meta_path = bucket_dir / "_meta.json"
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text())
    except json.JSONDecodeError:
        return False
    return isinstance(meta, dict) and "error" not in meta
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `python3 -m pytest tests/test_fetch.py -v`
Expected: all pass (previous 8 + new 5).

- [ ] **Step 5: Commit**

```bash
git add gh_contributions/fetch.py tests/test_fetch.py
git commit -m "fetch: add bucket completion detection"
```

---

## Task 4: Refactor metrics to walk monthly buckets; migrate fixtures

**Files:**
- Modify: `gh_contributions/metrics.py` (large refactor)
- Modify: `tests/test_metrics.py`
- Modify (move + edit): all fixtures under `tests/fixtures/` (`authoring/`, `collaboration/`, `team_share/`, `empty_repo/`, `truncated/`)

**Interfaces:**
- Consumes: `_months_between` from Task 2, `Config` (no `until`) from Task 1.
- Produces:
  - `compute(raw_root: Path, config: Config, *, today: date | None = None) -> dict` — `raw_root` is `out/raw/` (contains `<YYYY-MM>/` subdirs). `today` defaults to `datetime.now(timezone.utc).date()`.
  - `_load_endpoint(raw_root: Path, months: list[str], owner: str, name: str, filename: str) -> list[dict]` — concatenates JSON arrays across in-window monthly buckets, skipping missing dirs and errored `_meta.json`. Returns `[]` if nothing found.
  - `_load_reviews(raw_root: Path, months: list[str], owner: str, name: str) -> dict[int, list[dict]]` — merges `reviews/<pr>.json` files across months, keyed by PR number; duplicate keys take last-writer-wins.
  - `_month_status(raw_root: Path, month: str, owner: str, name: str) -> tuple[str, dict | str | None]` — returns `('good', meta_dict)` when the bucket has a valid `_meta.json`, `('error', reason_str)` when `_meta.json` has an `error` key or is malformed, `('absent', None)` when the bucket directory or `_meta.json` is missing. Absent buckets are treated as gaps (contribute nothing, no error); errored buckets surface as partial-error.

- [ ] **Step 1: Migrate the five existing fixture directories**

For each of `authoring`, `collaboration`, `team_share`, `empty_repo`, `truncated`:

```bash
cd tests/fixtures/<name>
mkdir raw/2026-02
git mv raw/acme__api raw/2026-02/acme__api
```

Then update each fixture's `config.yml`:
- Delete the `until:` line entirely.
- Change `since: 2026-01-01` to `since: 2026-02-01`.

(Verify with `ls tests/fixtures/authoring/raw/2026-02/acme__api/` — should show `_meta.json`, `commits.json`, etc.)

- [ ] **Step 2: Update the test helper in `tests/test_metrics.py`**

Replace the file's `_load` helper and update `test_run_metadata_present` to a fixed today:

```python
from datetime import date

FIXTURES = Path(__file__).parent / "fixtures"


def _load(fixture: str, today: date = date(2026, 2, 28)):
    cfg = load_config(str(FIXTURES / fixture / "config.yml"))
    return compute(FIXTURES / fixture / "raw", cfg, today=today)


def test_run_metadata_present() -> None:
    out = _load("authoring")
    assert out["run"]["since"] == "2026-02-01"
    assert out["run"]["until"] == "2026-02-28"
    assert out["run"]["metrics_layers"] == ["authoring"]
    assert "generated_at" in out["run"]
```

Update the two dynamic-fixture tests (`test_team_share_zero_denominator_is_null` and `test_team_share_pr_reviews_windowed`). Replace both bodies to use the new layout:

```python
def test_team_share_zero_denominator_is_null(tmp_path) -> None:
    from gh_contributions.config import Config

    cfg = Config(
        usernames=["alice"],
        repos=["acme/api"],
        since=date(2026, 2, 1),
        metrics=["team_share"],
    )
    bucket = tmp_path / "2026-02" / "acme__api"
    bucket.mkdir(parents=True)
    (bucket / "_meta.json").write_text("{}")
    for f in ("commits.json", "prs_by_created.json", "prs_by_merged.json",
              "prs_updated.json", "review_comments.json", "issue_comments.json"):
        (bucket / f).write_text("[]")
    (bucket / "reviews").mkdir()

    out = compute(tmp_path, cfg, today=date(2026, 2, 28))
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
    import json as _json

    cfg = Config(
        usernames=["alice"],
        repos=["acme/api"],
        since=date(2026, 2, 1),
        metrics=["team_share"],
    )
    bucket = tmp_path / "2026-02" / "acme__api"
    bucket.mkdir(parents=True)
    (bucket / "_meta.json").write_text("{}")
    for f in ("commits.json", "prs_by_created.json", "prs_by_merged.json",
              "prs_updated.json", "review_comments.json", "issue_comments.json"):
        (bucket / f).write_text("[]")
    (bucket / "reviews").mkdir()
    (bucket / "reviews" / "1.json").write_text(_json.dumps([
        {"user": {"login": "alice"}, "state": "APPROVED", "submitted_at": "2026-02-10T10:00:00Z"},
        {"user": {"login": "eve"},   "state": "APPROVED", "submitted_at": "2026-01-15T10:00:00Z"},
    ]))

    share = compute(tmp_path, cfg, today=date(2026, 2, 28))["repos"]["acme/api"]["team_share"]
    assert share["pr"]["team"]["APPROVED"]  == 1
    assert share["pr"]["total"]["APPROVED"] == 1
```

(Note: the second review in `1.json` is dated 2026-01-15, which is now outside the fixture window `[2026-02-01, 2026-02-28]`, so it is still excluded — same test intent.)

- [ ] **Step 3: Run tests and verify they fail**

Run: `python3 -m pytest tests/test_metrics.py -v`
Expected: many failures — `compute` doesn't accept `today` kwarg; `Config` doesn't accept `until` (already fixed in Task 1). Tests point at `raw_root = raw/` which now contains `2026-02/` subdir, but current metrics code expects `raw_root / <owner>__<repo>`.

- [ ] **Step 4: Refactor `metrics.py`**

Replace the file contents:

```python
"""Pure computation of team-activity metrics from on-disk raw pages."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .fetch import _months_between


def compute(raw_root: Path, config: Config, *, today: date | None = None) -> dict:
    if today is None:
        today = datetime.now(timezone.utc).date()
    months = _months_between(config.since, today)
    result: dict[str, Any] = {
        "run": {
            "since": config.since.isoformat(),
            "until": today.isoformat(),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "metrics_layers": list(config.metrics),
        },
        "repos": {},
    }
    for repo in config.repos:
        result["repos"][repo] = _compute_repo(raw_root, months, repo, config, today)
    return result


def _compute_repo(
    raw_root: Path,
    months: list[str],
    repo: str,
    config: Config,
    today: date,
) -> dict:
    owner, name = repo.split("/", 1)

    statuses: dict[str, tuple[str, dict | str | None]] = {
        m: _month_status(raw_root, m, owner, name) for m in months
    }
    good_months    = [m for m, s in statuses.items() if s[0] == "good"]
    errored_months = [m for m, s in statuses.items() if s[0] == "error"]
    # 'absent' months are silently treated as gaps.

    if months and not good_months and errored_months:
        reasons = sorted({str(statuses[m][1]) for m in errored_months})
        reason = reasons[0] if len(reasons) == 1 else "; ".join(reasons)
        return {
            "per_user": None,
            "team_share": None,
            "truncated": None,
            "error": reason,
        }

    per_user: dict[str, dict] = {u: {} for u in config.usernames}
    truncated: dict[str, bool] = {}
    error: str | None = None
    if errored_months:
        parts = [f"{m} ({statuses[m][1]})" for m in errored_months]
        error = "partial: failed months: " + ", ".join(parts)

    for m in good_months:
        meta = statuses[m][1]
        if isinstance(meta, dict):
            for endpoint, entry in meta.items():
                if isinstance(entry, dict) and entry.get("truncated"):
                    truncated[endpoint] = True

    out: dict[str, Any] = {
        "per_user": per_user,
        "team_share": None,
        "truncated": truncated,
        "error": error,
    }

    if "authoring" in config.metrics:
        _apply_authoring(raw_root, good_months, owner, name, config, per_user)

    if "collaboration" in config.metrics:
        _apply_collaboration(raw_root, good_months, owner, name, config, today, per_user)

    if "team_share" in config.metrics:
        _apply_team_share(raw_root, good_months, owner, name, config, today, out)

    return out


def _month_status(
    raw_root: Path,
    month: str,
    owner: str,
    name: str,
) -> tuple[str, dict | str | None]:
    """('good', meta_dict) | ('error', reason_str) | ('absent', None)."""
    repo_dir = raw_root / month / f"{owner}__{name}"
    meta_path = repo_dir / "_meta.json"
    if not repo_dir.exists() or not meta_path.exists():
        return ("absent", None)
    try:
        meta = json.loads(meta_path.read_text())
    except json.JSONDecodeError:
        return ("error", "malformed")
    if not isinstance(meta, dict):
        return ("error", "malformed")
    if "error" in meta:
        return ("error", str(meta["error"]))
    return ("good", meta)


def _load_endpoint(
    raw_root: Path,
    months: list[str],
    owner: str,
    name: str,
    filename: str,
) -> list[dict]:
    out: list[dict] = []
    for m in months:
        path = raw_root / m / f"{owner}__{name}" / filename
        if not path.exists():
            continue
        data = _read_json(path, default=[])
        if isinstance(data, list):
            out.extend(data)
    return out


def _load_reviews(
    raw_root: Path,
    months: list[str],
    owner: str,
    name: str,
) -> dict[int, list[dict]]:
    merged: dict[int, list[dict]] = {}
    for m in months:
        reviews_dir = raw_root / m / f"{owner}__{name}" / "reviews"
        if not reviews_dir.is_dir():
            continue
        for review_file in sorted(reviews_dir.glob("*.json")):
            try:
                pr_number = int(review_file.stem)
            except ValueError:
                continue
            data = _read_json(review_file, default=[])
            if isinstance(data, list):
                merged[pr_number] = data
    return merged


def _apply_authoring(
    raw_root: Path,
    months: list[str],
    owner: str,
    name: str,
    config: Config,
    per_user: dict[str, dict],
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
        for item in _load_endpoint(raw_root, months, owner, name, src):
            login = _author_login(item, src)
            if login in team:
                counts[login][key] += 1

    for u in team:
        per_user[u]["authoring"] = counts[u]


def _author_login(item: dict, src: str) -> str | None:
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


_REVIEW_STATES = ("APPROVED", "CHANGES_REQUESTED", "COMMENTED")


def _window_bounds(config: Config, today: date) -> tuple[datetime, datetime]:
    lo = datetime.combine(config.since, time.min, tzinfo=timezone.utc)
    hi = datetime.combine(today, time(23, 59, 59), tzinfo=timezone.utc)
    return lo, hi


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _in_window(ts: str | None, lo: datetime, hi: datetime) -> bool:
    d = _parse_ts(ts)
    return d is not None and lo <= d <= hi


def _apply_collaboration(
    raw_root: Path,
    months: list[str],
    owner: str,
    name: str,
    config: Config,
    today: date,
    per_user: dict[str, dict],
) -> None:
    team = set(config.usernames)
    lo, hi = _window_bounds(config, today)

    collab = {u: {
        "reviews_given": {s: 0 for s in _REVIEW_STATES},
        "review_comments": 0,
        "pr_conversation_comments": 0,
        "issue_comments": 0,
        "cross_team_reviews": 0,
    } for u in team}

    pr_author_by_number: dict[int, str] = {}
    for pr in _load_endpoint(raw_root, months, owner, name, "prs_updated.json"):
        num = pr.get("number")
        user = pr.get("user") or {}
        if isinstance(num, int) and isinstance(user, dict):
            pr_author_by_number[num] = user.get("login") or ""

    known_pr_numbers = set(pr_author_by_number)

    reviews_by_pr = _load_reviews(raw_root, months, owner, name)
    for pr_number, reviews in reviews_by_pr.items():
        pr_author = pr_author_by_number.get(pr_number, "")
        for r in reviews:
            state = r.get("state")
            if state not in _REVIEW_STATES:
                continue
            if not _in_window(r.get("submitted_at"), lo, hi):
                continue
            reviewer = ((r.get("user") or {}).get("login")) or ""
            if reviewer not in team:
                continue
            collab[reviewer]["reviews_given"][state] += 1
            if pr_author and pr_author not in team:
                collab[reviewer]["cross_team_reviews"] += 1

    for c in _load_endpoint(raw_root, months, owner, name, "review_comments.json"):
        if not _in_window(c.get("created_at"), lo, hi):
            continue
        login = ((c.get("user") or {}).get("login")) or ""
        if login in team:
            collab[login]["review_comments"] += 1

    for c in _load_endpoint(raw_root, months, owner, name, "issue_comments.json"):
        if not _in_window(c.get("created_at"), lo, hi):
            continue
        login = ((c.get("user") or {}).get("login")) or ""
        if login not in team:
            continue
        parent = _parent_number(c.get("issue_url"))
        if parent is not None and parent in known_pr_numbers:
            collab[login]["pr_conversation_comments"] += 1
        else:
            collab[login]["issue_comments"] += 1

    for u in team:
        per_user[u]["collaboration"] = collab[u]


def _parent_number(issue_url: str | None) -> int | None:
    if not issue_url:
        return None
    tail = issue_url.rstrip("/").rsplit("/", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return None


def _apply_team_share(
    raw_root: Path,
    months: list[str],
    owner: str,
    name: str,
    config: Config,
    today: date,
    out: dict,
) -> None:
    team = set(config.usernames)
    lo, hi = _window_bounds(config, today)

    commits_team = 0
    commits_total = 0
    for c in _load_endpoint(raw_root, months, owner, name, "commits.json"):
        commits_total += 1
        if _author_login(c, "commits.json") in team:
            commits_team += 1

    opened_team, opened_total = 0, 0
    for p in _load_endpoint(raw_root, months, owner, name, "prs_by_created.json"):
        opened_total += 1
        if _author_login(p, "prs_by_created.json") in team:
            opened_team += 1

    merged_team, merged_total = 0, 0
    for p in _load_endpoint(raw_root, months, owner, name, "prs_by_merged.json"):
        merged_total += 1
        if _author_login(p, "prs_by_merged.json") in team:
            merged_team += 1

    rev_team = {s: 0 for s in _REVIEW_STATES}
    rev_total = {s: 0 for s in _REVIEW_STATES}
    for reviews in _load_reviews(raw_root, months, owner, name).values():
        for r in reviews:
            state = r.get("state")
            if state not in _REVIEW_STATES:
                continue
            if not _in_window(r.get("submitted_at"), lo, hi):
                continue
            rev_total[state] += 1
            if ((r.get("user") or {}).get("login")) in team:
                rev_team[state] += 1

    rc_team, rc_total = 0, 0
    for c in _load_endpoint(raw_root, months, owner, name, "review_comments.json"):
        if not _in_window(c.get("created_at"), lo, hi):
            continue
        rc_total += 1
        if ((c.get("user") or {}).get("login")) in team:
            rc_team += 1

    prs_updated = _load_endpoint(raw_root, months, owner, name, "prs_updated.json")
    known_pr_numbers = {
        p.get("number") for p in prs_updated if isinstance(p.get("number"), int)
    }
    prc_team, prc_total = 0, 0
    ic_team, ic_total = 0, 0
    for c in _load_endpoint(raw_root, months, owner, name, "issue_comments.json"):
        if not _in_window(c.get("created_at"), lo, hi):
            continue
        parent = _parent_number(c.get("issue_url"))
        is_pr_conv = parent is not None and parent in known_pr_numbers
        login = ((c.get("user") or {}).get("login")) or ""
        is_team = login in team
        if is_pr_conv:
            prc_total += 1
            if is_team:
                prc_team += 1
        else:
            ic_total += 1
            if is_team:
                ic_team += 1

    def _layer(team_map: dict[str, int], total_map: dict[str, int]) -> dict:
        t = sum(team_map.values())
        n = sum(total_map.values())
        return {"team": team_map, "total": total_map, "share": (t / n) if n else None}

    out["team_share"] = {
        "commits": _layer(
            {"commits": commits_team},
            {"commits": commits_total},
        ),
        "pr": _layer(
            {
                "pull_requests_opened": opened_team,
                "pull_requests_merged": merged_team,
                "APPROVED":             rev_team["APPROVED"],
                "CHANGES_REQUESTED":    rev_team["CHANGES_REQUESTED"],
                "COMMENTED":            rev_team["COMMENTED"],
            },
            {
                "pull_requests_opened": opened_total,
                "pull_requests_merged": merged_total,
                "APPROVED":             rev_total["APPROVED"],
                "CHANGES_REQUESTED":    rev_total["CHANGES_REQUESTED"],
                "COMMENTED":            rev_total["COMMENTED"],
            },
        ),
        "comments": _layer(
            {
                "review_comments":          rc_team,
                "pr_conversation_comments": prc_team,
                "issue_comments":           ic_team,
            },
            {
                "review_comments":          rc_total,
                "pr_conversation_comments": prc_total,
                "issue_comments":           ic_total,
            },
        ),
    }
```

- [ ] **Step 5: Run tests and verify they pass**

Run: `python3 -m pytest tests/test_metrics.py -v`
Expected: all pass (the `empty_repo` error case now surfaces via `_meta.json` at `raw/2026-02/acme__api/_meta.json` — unchanged content).

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add gh_contributions/metrics.py tests/test_metrics.py tests/fixtures/
git commit -m "metrics: walk monthly buckets; migrate fixtures to per-month layout"
```

---

## Task 5: Rewire `run.py` for month-outer / repo-inner iteration

**Files:**
- Modify: `gh_contributions/run.py`
- Modify: `tests/test_fetch.py` (append run-loop tests)

**Interfaces:**
- Consumes: `Config`, `_months_between`, `_month_bounds`, `_is_bucket_complete`, `fetch_repo`, `compute`.
- Produces: `main(argv)` that:
  1. Loads config.
  2. Computes `today = datetime.now(timezone.utc).date()` and `months = _months_between(cfg.since, today)`.
  3. Creates `raw_root = Path("out/raw")` and `run_out = Path("out") / <timestamp>`.
  4. For each month `M` in months, for each repo `R`: if `_is_bucket_complete(raw_root / M / <owner>__<name>)` print `skip <M> <repo> (cached)` else call `fetch_repo(client, R, month_start, month_end, raw_root / M)`.
  5. Calls `compute(raw_root, cfg, today=today)` and writes `metrics.json` under `run_out`.
  6. Return codes unchanged (0 success, 1 no repos produced metrics, 2 fatal).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fetch.py`:

```python
from unittest.mock import MagicMock, patch

from gh_contributions.config import Config


def _cfg(since: date) -> Config:
    return Config(
        usernames=["alice"],
        repos=["acme/api"],
        since=since,
        metrics=["authoring"],
    )


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

    # Config: since 2026-05-01. Freeze today to 2026-07-02 → months = [05, 06, 07].
    fake_today = date(2026, 7, 2)

    class _FakeDT:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 7, 2, 12, 0, 0, tzinfo=tz or timezone.utc)

    monkeypatch.setattr(run_mod, "datetime", _FakeDT)
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.chdir(tmp_path)

    # Pre-populate May and June as complete; July is missing.
    _write_complete_bucket(tmp_path / "out" / "raw" / "2026-05" / "acme__api")
    _write_complete_bucket(tmp_path / "out" / "raw" / "2026-06" / "acme__api")

    # Write a config.yml at tmp_path root.
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
    # Only July was fetched.
    assert fetch_calls == [("acme/api", date(2026, 7, 1), fake_today, "2026-07")]

    # run out dir written.
    run_dirs = [p for p in (tmp_path / "out").iterdir() if p.name != "raw"]
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "metrics.json").exists()
```

Also assert missing-token exit:

```python
def test_run_missing_token_returns_2(tmp_path, monkeypatch) -> None:
    from gh_contributions import run as run_mod

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yml").write_text(
        "usernames: [alice]\nrepos: [acme/api]\nsince: 2026-05-01\nmetrics: [authoring]\n"
    )
    assert run_mod.main([]) == 2
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python3 -m pytest tests/test_fetch.py -v`
Expected: `test_run_skips_complete_buckets_and_fetches_missing` fails — current `run.py` doesn't enumerate months or skip.

- [ ] **Step 3: Rewrite `gh_contributions/run.py`**

Replace file contents:

```python
"""Entry point: config -> monthly fetch -> compute -> metrics.json."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import ConfigError, load_config
from .fetch import _is_bucket_complete, _month_bounds, _months_between, fetch_repo
from .github_client import AuthError, GitHubClient
from .metrics import compute


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    config_path = argv[0] if argv else "config.yml"

    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"config not found: {config_path}", file=sys.stderr)
        return 2

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN env var is required", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    today = now.date()
    run_id = now.strftime("%Y-%m-%dT%H%M%SZ")

    run_out = Path("out") / run_id
    run_out.mkdir(parents=True, exist_ok=True)
    raw_root = Path("out") / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)
    print(f"run dir: {run_out}", file=sys.stderr)
    print(f"raw cache: {raw_root}", file=sys.stderr)

    months = _months_between(cfg.since, today)
    if not months or not cfg.repos:
        if not cfg.repos:
            print("no repos configured; writing empty metrics.json", file=sys.stderr)
        else:
            print(f"since ({cfg.since}) is after today; writing empty metrics.json", file=sys.stderr)
        _write_metrics(run_out, compute(raw_root, cfg, today=today))
        return 0

    client = GitHubClient(token)
    for month in months:
        month_start, month_end = _month_bounds(month, today)
        month_dir = raw_root / month
        month_dir.mkdir(parents=True, exist_ok=True)
        for repo in cfg.repos:
            owner, name = repo.split("/", 1)
            bucket = month_dir / f"{owner}__{name}"
            if _is_bucket_complete(bucket):
                print(f"skip {month} {repo} (cached)", file=sys.stderr)
                continue
            print(f"fetching {month} {repo}", file=sys.stderr)
            try:
                fetch_repo(client, repo, month_start, month_end, month_dir)
            except AuthError as exc:
                print(f"auth failed: {exc}", file=sys.stderr)
                return 2

    result = compute(raw_root, cfg, today=today)
    _write_metrics(run_out, result)

    ok_count = sum(
        1 for r in result["repos"].values() if r.get("per_user") is not None
    )
    if ok_count == 0:
        print("no repos produced metrics", file=sys.stderr)
        return 1
    return 0


def _write_metrics(out_dir: Path, result: dict) -> None:
    path = out_dir / "metrics.json"
    path.write_text(json.dumps(result, indent=2))
    print(f"wrote {path}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add gh_contributions/run.py tests/test_fetch.py
git commit -m "run: iterate months, skip completed buckets"
```

---

## Task 6: Multi-month fixture and merge tests

**Files:**
- Create: `tests/fixtures/multi_month/config.yml`
- Create: `tests/fixtures/multi_month/raw/2026-05/acme__api/{_meta,commits,prs_by_created,prs_by_merged,issues_by_created,prs_updated,review_comments,issue_comments}.json`
- Create: `tests/fixtures/multi_month/raw/2026-05/acme__api/reviews/1.json`
- Create: `tests/fixtures/multi_month/raw/2026-06/acme__api/…` (same set of files)
- Create: `tests/fixtures/multi_month/raw/2026-06/acme__api/reviews/1.json` (same content as May)
- Modify: `tests/test_metrics.py` (append multi-month tests)

**Interfaces:** consumes `compute` from Task 4.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_metrics.py`:

```python
def test_multi_month_authoring_sums_across_months() -> None:
    out = _load("multi_month", today=date(2026, 6, 30))
    users = out["repos"]["acme/api"]["per_user"]
    # May: alice 1 commit, bob 1 commit. June: alice 2 commits, bob 0. Total: alice 3, bob 1.
    assert users["alice"]["authoring"]["commits"] == 3
    assert users["bob"]["authoring"]["commits"] == 1


def test_multi_month_truncation_ored_across_months() -> None:
    out = _load("multi_month", today=date(2026, 6, 30))
    # May's _meta marks commits truncated; June's does not.
    assert out["repos"]["acme/api"]["truncated"].get("commits") is True


def test_multi_month_pr_reviews_deduplicated_by_pr_number() -> None:
    out = _load("multi_month", today=date(2026, 6, 30))
    # PR 1 has reviews/1.json in BOTH May and June with the same single APPROVED review.
    # Merge is last-writer-wins keyed by PR number; must not double-count.
    share = out["repos"]["acme/api"]["team_share"]
    assert share["pr"]["total"]["APPROVED"] == 1
```

- [ ] **Step 2: Create the fixture files**

`tests/fixtures/multi_month/config.yml`:

```yaml
usernames:
  - alice
  - bob
repos:
  - acme/api
since: 2026-05-01
metrics:
  - authoring
  - team_share
```

For **May** (`tests/fixtures/multi_month/raw/2026-05/acme__api/`):

`_meta.json`:

```json
{
  "commits":          {"total_count": 1500, "truncated": true},
  "prs_by_created":   {"total_count": 0,    "truncated": false},
  "prs_by_merged":    {"total_count": 0,    "truncated": false},
  "issues_by_created":{"total_count": 0,    "truncated": false},
  "prs_updated":      {"total_count": 1,    "truncated": false},
  "reviews":          {"total_count": 1,    "truncated": false},
  "review_comments":  {"total_count": 0,    "truncated": false},
  "issue_comments":   {"total_count": 0,    "truncated": false}
}
```

`commits.json`:

```json
[
  {"sha": "m1", "author": {"login": "alice"}, "commit": {"author": {"date": "2026-05-05T10:00:00Z"}}},
  {"sha": "m2", "author": {"login": "bob"},   "commit": {"author": {"date": "2026-05-06T10:00:00Z"}}}
]
```

`prs_by_created.json`, `prs_by_merged.json`, `issues_by_created.json`, `review_comments.json`, `issue_comments.json`: `[]`

`prs_updated.json`:

```json
[
  {"number": 1, "user": {"login": "alice"}, "updated_at": "2026-05-20T10:00:00Z"}
]
```

`reviews/1.json`:

```json
[
  {"user": {"login": "bob"}, "state": "APPROVED", "submitted_at": "2026-05-21T10:00:00Z"}
]
```

For **June** (`tests/fixtures/multi_month/raw/2026-06/acme__api/`):

`_meta.json`:

```json
{
  "commits":          {"total_count": 2,  "truncated": false},
  "prs_by_created":   {"total_count": 0,  "truncated": false},
  "prs_by_merged":    {"total_count": 0,  "truncated": false},
  "issues_by_created":{"total_count": 0,  "truncated": false},
  "prs_updated":      {"total_count": 1,  "truncated": false},
  "reviews":          {"total_count": 1,  "truncated": false},
  "review_comments":  {"total_count": 0,  "truncated": false},
  "issue_comments":   {"total_count": 0,  "truncated": false}
}
```

`commits.json`:

```json
[
  {"sha": "j1", "author": {"login": "alice"}, "commit": {"author": {"date": "2026-06-05T10:00:00Z"}}},
  {"sha": "j2", "author": {"login": "alice"}, "commit": {"author": {"date": "2026-06-06T10:00:00Z"}}}
]
```

`prs_by_created.json`, `prs_by_merged.json`, `issues_by_created.json`, `review_comments.json`, `issue_comments.json`: `[]`

`prs_updated.json` (same PR 1, touched again in June):

```json
[
  {"number": 1, "user": {"login": "alice"}, "updated_at": "2026-06-15T10:00:00Z"}
]
```

`reviews/1.json` (identical content to May's copy):

```json
[
  {"user": {"login": "bob"}, "state": "APPROVED", "submitted_at": "2026-05-21T10:00:00Z"}
]
```

- [ ] **Step 3: Run tests and verify they pass**

Run: `python3 -m pytest tests/test_metrics.py -v -k multi_month`
Expected: all 3 pass.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/multi_month tests/test_metrics.py
git commit -m "test: multi-month fixture and merge behavior"
```

---

## Task 7: Missing-month fixture and gap tolerance

**Files:**
- Create: `tests/fixtures/missing_month/config.yml`
- Create: `tests/fixtures/missing_month/raw/2026-05/acme__api/…`
- Create: `tests/fixtures/missing_month/raw/2026-07/acme__api/…` (June absent)
- Modify: `tests/test_metrics.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_metrics.py`:

```python
def test_missing_month_in_the_middle_contributes_zero_no_error() -> None:
    out = _load("missing_month", today=date(2026, 7, 31))
    repo = out["repos"]["acme/api"]
    # May: 1 commit by alice. July: 1 commit by alice. June bucket absent.
    # Missing months in-window (with no bucket at all) are treated as gaps, not errors.
    assert repo["error"] is None
    assert repo["per_user"]["alice"]["authoring"]["commits"] == 2
```

- [ ] **Step 2: Create the fixture files**

`tests/fixtures/missing_month/config.yml`:

```yaml
usernames:
  - alice
repos:
  - acme/api
since: 2026-05-01
metrics:
  - authoring
```

For **May** (`raw/2026-05/acme__api/`):

`_meta.json`:

```json
{
  "commits":          {"total_count": 1, "truncated": false},
  "prs_by_created":   {"total_count": 0, "truncated": false},
  "prs_by_merged":    {"total_count": 0, "truncated": false},
  "issues_by_created":{"total_count": 0, "truncated": false},
  "prs_updated":      {"total_count": 0, "truncated": false},
  "reviews":          {"total_count": 0, "truncated": false},
  "review_comments":  {"total_count": 0, "truncated": false},
  "issue_comments":   {"total_count": 0, "truncated": false}
}
```

`commits.json`:

```json
[{"sha": "m1", "author": {"login": "alice"}, "commit": {"author": {"date": "2026-05-10T10:00:00Z"}}}]
```

`prs_by_created.json`, `prs_by_merged.json`, `issues_by_created.json`, `prs_updated.json`, `review_comments.json`, `issue_comments.json`: `[]`

For **July** (`raw/2026-07/acme__api/`), same shape but with:

`commits.json`:

```json
[{"sha": "j1", "author": {"login": "alice"}, "commit": {"author": {"date": "2026-07-10T10:00:00Z"}}}]
```

And `_meta.json` with `commits.total_count = 1`, all others 0.

- [ ] **Step 3: Run tests and verify they pass**

Run: `python3 -m pytest tests/test_metrics.py -v -k missing_month`
Expected: passes. (`good_months = ["2026-05", "2026-07"]`, `errored_months = []`, absent June is a silent gap.)

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/missing_month tests/test_metrics.py
git commit -m "test: absent month bucket in the middle of window is a gap, not an error"
```

---

## Task 8: Partial-error fixture and reporting

**Files:**
- Create: `tests/fixtures/partial_error/config.yml`
- Create: `tests/fixtures/partial_error/raw/2026-05/acme__api/…`, `2026-06/acme__api/…`
- Create: `tests/fixtures/partial_error/raw/2026-07/acme__api/_meta.json` (error only)
- Modify: `tests/test_metrics.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_metrics.py`:

```python
def test_partial_error_reports_partial_and_keeps_good_data() -> None:
    out = _load("partial_error", today=date(2026, 7, 31))
    repo = out["repos"]["acme/api"]
    # May + June good (1 commit each by alice); July errored (rate_limited).
    assert repo["per_user"]["alice"]["authoring"]["commits"] == 2
    assert repo["error"] == "partial: failed months: 2026-07 (rate_limited)"


def test_partial_error_all_months_failed_surfaces_single_error() -> None:
    out = _load("partial_error_all_bad", today=date(2026, 7, 31))
    repo = out["repos"]["acme/api"]
    assert repo["per_user"] is None
    assert repo["error"] == "not_found"
```

- [ ] **Step 2: Create the `partial_error` fixture**

`tests/fixtures/partial_error/config.yml`:

```yaml
usernames: [alice]
repos: [acme/api]
since: 2026-05-01
metrics: [authoring]
```

`raw/2026-05/acme__api/` — same shape as missing-month's May bucket (`commits.json` has one alice commit dated `2026-05-*`, `_meta.json` has all zeros with `commits.total_count = 1`, all other endpoint files `[]`).

`raw/2026-06/acme__api/` — same, with one alice commit dated `2026-06-*`.

`raw/2026-07/acme__api/_meta.json`:

```json
{"error": "rate_limited"}
```

No other files under July's bucket (matches what `_write_error` produces).

- [ ] **Step 3: Create the `partial_error_all_bad` fixture**

`tests/fixtures/partial_error_all_bad/config.yml`:

```yaml
usernames: [alice]
repos: [acme/api]
since: 2026-05-01
metrics: [authoring]
```

`raw/2026-05/acme__api/_meta.json`, `raw/2026-06/acme__api/_meta.json`, `raw/2026-07/acme__api/_meta.json` all contain:

```json
{"error": "not_found"}
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `python3 -m pytest tests/test_metrics.py -v -k partial_error`
Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/partial_error tests/fixtures/partial_error_all_bad tests/test_metrics.py
git commit -m "test: partial-error surfacing for mixed and all-failed months"
```

---

## Task 9: README updates

**Files:**
- Modify: `README.md`

**Interfaces:** documentation only.

- [ ] **Step 1: Update `## Configure` and add a `## Raw-data cache` section**

Replace the sample config block in `README.md`:

```yaml
usernames:
  - alice
  - bob
repos:
  - acme/api
since: 2026-01-01
metrics:
  - authoring
  - collaboration
  - team_share
```

Add a new section after `## Run` (before `## Report`):

```markdown
## Raw-data cache

Raw API responses are stored under `out/raw/<YYYY-MM>/<owner>__<repo>/`, one bucket
per (month, repo). Runs enumerate months from `since` to today (UTC) and only
fetch buckets that are not already complete on disk. A bucket is complete when
its `_meta.json` exists and contains no `error` key.

To force a refresh of a specific bucket, delete it and re-run:

```bash
rm -rf out/raw/2026-07/acme__api
python3 -m gh_contributions.run
```

The current calendar month is **not** auto-refreshed — once its bucket is on disk
it stays until you delete it. Delete `out/raw/<current-month>/` (or a single
repo inside it) between runs to pick up new activity within the current month.
```

- [ ] **Step 2: Verify the file renders correctly**

Run: `cat README.md | head -80` and eyeball. No test runner change.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document monthly raw-data cache and refresh workflow"
```

---

## Self-Review Notes

- **Spec coverage** — every section of `docs/superpowers/specs/2026-07-02-monthly-raw-data-cache-design.md` maps to a task:
  - Layout & config change → Tasks 1, 5, 9.
  - Fetch flow (month enumeration, per-month, self-contained) → Tasks 2, 5.
  - Completion detection → Task 3.
  - Metrics computation (merging, truncation OR, partial error) → Tasks 4, 7, 8.
  - Error handling / edge cases → Tasks 5 (auth/no-token, empty enumeration), 7 (missing bucket), 8 (partial + full error).
  - Testing (multi_month, missing_month, partial_error, fixture migration) → Tasks 4, 6, 7, 8.
  - README → Task 9.
- **Type consistency** — `compute(raw_root, config, *, today=None)` signature is used consistently in Tasks 4, 5, 6, 7, 8. `_months_between(since, today)` used in Tasks 2, 4, 5. `_is_bucket_complete(bucket_dir)` used in Tasks 3, 5. `_month_bounds(month, today)` used in Tasks 2, 5.
- **No placeholders** — every step contains the code to write or the exact command to run.
- **TDD discipline** — every code-producing task has a "write failing test" step before implementation.
