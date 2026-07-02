# Exclude Current Month — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Never fetch or cache raw GitHub data for the current calendar month; end the analysis window on the last day of the previous month (UTC).

**Architecture:** Introduce a single `_effective_end(today)` helper in `gh_contributions/fetch.py` and use it as the upper bound of both `_months_between` (month enumeration) and `metrics._window_bounds` (event-window filter). One boundary, no drift.

**Tech Stack:** Python 3.10+, `pytest`. No new dependencies.

## Global Constraints

- All date arithmetic uses UTC. "today" = `datetime.now(timezone.utc).date()`.
- No changes to fetcher HTTP behavior, `fetch_repo` signature, or `metrics.json` schema keys.
- No new runtime dependencies.
- Live GitHub calls are never made from tests.

---

## File Structure

Modified files:

- `gh_contributions/fetch.py` — add `_effective_end`; update `_months_between`.
- `gh_contributions/metrics.py` — use `_effective_end` in `_window_bounds`.
- `gh_contributions/run.py` — refine the empty-months message.
- `config.yml` — comment above `since`.
- `README.md` — sample-config comment; rewrite Raw-data cache caveat.
- `tests/test_fetch.py` — new `_effective_end` tests, `_months_between` current-month test, split of `test_run_skips_complete_buckets_and_fetches_missing`.
- `tests/test_metrics.py` — bump every `today=` argument by one month.

No files created.

---

## Task 1: Add `_effective_end` helper

**Files:**
- Modify: `gh_contributions/fetch.py` (append new helper next to `_months_between`)
- Modify: `tests/test_fetch.py` (append new tests)

**Interfaces:**
- Consumes: nothing new.
- Produces: `_effective_end(today: date) -> date` — returns the last day of the calendar month preceding `today`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fetch.py` (before the `from datetime import datetime, timezone` block near the run-mode helpers, i.e. right after the last `_is_bucket_complete` test):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_fetch.py -v -k effective_end`
Expected: 4 failures with `ImportError` / `AttributeError` for `_effective_end`.

- [ ] **Step 3: Implement `_effective_end`**

Insert this helper in `gh_contributions/fetch.py` immediately above `def _months_between`:

```python
def _effective_end(today: date) -> date:
    """Last day of the calendar month preceding ``today`` (UTC)."""
    first_of_current = date(today.year, today.month, 1)
    return first_of_current - timedelta(days=1)
```

Add `timedelta` to the existing `from datetime import ...` line at the top of `fetch.py`. (Current imports include `date`; append `, timedelta`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_fetch.py -v -k effective_end`
Expected: 4 passed.

Then full suite: `python3 -m pytest -q`
Expected: 72 passed (existing 68 + 4 new).

- [ ] **Step 5: Commit**

```bash
git add gh_contributions/fetch.py tests/test_fetch.py
git commit -m "fetch: add _effective_end helper"
```

---

## Task 2: Wire `_effective_end` into month enumeration, metrics window, and run message

**Files:**
- Modify: `gh_contributions/fetch.py` (`_months_between`)
- Modify: `gh_contributions/metrics.py` (`_window_bounds`, `compute` header)
- Modify: `gh_contributions/run.py` (empty-months log message)
- Modify: `tests/test_fetch.py` (`_months_between` new-behavior tests; run-mode tests)
- Modify: `tests/test_metrics.py` (bump every `today=` argument by one month)

**Interfaces:**
- Consumes: `_effective_end` from Task 1.
- Produces: `_months_between(since, today)` returns `[]` when `since > _effective_end(today)`; otherwise months from `since`'s month through `_effective_end(today)`'s month, inclusive. `_window_bounds(config, today)` returns `hi = 23:59:59 UTC on _effective_end(today)`. `metrics.json` `run.until` = `_effective_end(today).isoformat()`.

- [ ] **Step 1: Write the failing tests — `_months_between`**

The rule for every case: after this change, `today` must be strictly in a later calendar month than the last month you expect in the enumeration (because `today`'s month is now excluded).

Update the four existing `_months_between` tests in `tests/test_fetch.py`:

1. `test_months_between_single_month` — bump `today` forward:
   ```python
   def test_months_between_single_month() -> None:
       assert _months_between(date(2026, 5, 15), date(2026, 6, 1)) == ["2026-05"]
   ```

2. `test_months_between_spans_year_boundary` — bump `today` forward:
   ```python
   def test_months_between_spans_year_boundary() -> None:
       assert _months_between(date(2025, 11, 30), date(2026, 3, 1)) == [
           "2025-11", "2025-12", "2026-01", "2026-02",
       ]
   ```

3. `test_months_between_since_after_today_returns_empty` — leave unchanged; still passes.

4. `test_months_between_since_first_day_of_month` — bump `today` forward:
   ```python
   def test_months_between_since_first_day_of_month() -> None:
       assert _months_between(date(2026, 5, 1), date(2026, 7, 1)) == ["2026-05", "2026-06"]
   ```

Append three new cases immediately after these:

```python
def test_months_between_excludes_current_month() -> None:
    # Today is mid-July; enumeration must stop at June.
    assert _months_between(date(2026, 5, 1), date(2026, 7, 15)) == ["2026-05", "2026-06"]


def test_months_between_since_in_current_month_is_empty() -> None:
    # Since is inside the current calendar month → no complete months yet.
    assert _months_between(date(2026, 7, 10), date(2026, 7, 15)) == []


def test_months_between_since_and_today_in_same_past_month_is_empty() -> None:
    # Both dates in the same calendar month → the month is "current" → excluded.
    assert _months_between(date(2026, 5, 5), date(2026, 5, 20)) == []
```

- [ ] **Step 2: Write the failing tests — run-mode split**

In `tests/test_fetch.py`, replace the existing `test_run_skips_complete_buckets_and_fetches_missing` test with **two** tests:

```python
def test_run_skips_all_complete_buckets(tmp_path, monkeypatch) -> None:
    from gh_contributions import run as run_mod

    class _FakeDT:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 8, 15, 12, 0, 0, tzinfo=tz or timezone.utc)

    monkeypatch.setattr(run_mod, "datetime", _FakeDT)
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.chdir(tmp_path)

    for month in ("2026-05", "2026-06", "2026-07"):
        _write_complete_bucket(tmp_path / "out" / "raw" / month / "acme__api")

    (tmp_path / "config.yml").write_text(
        "usernames: [alice]\nrepos: [acme/api]\nsince: 2026-05-01\nmetrics: [authoring]\n"
    )

    fetch_calls: list[tuple] = []

    def fake_fetch_repo(client, repo, since, until, out_dir):
        fetch_calls.append((repo, since, until, Path(out_dir).name))

    monkeypatch.setattr(run_mod, "fetch_repo", fake_fetch_repo)
    monkeypatch.setattr(run_mod, "GitHubClient", lambda token: MagicMock())

    rc = run_mod.main([])
    assert rc == 0
    assert fetch_calls == []


def test_run_fetches_missing_month(tmp_path, monkeypatch) -> None:
    from gh_contributions import run as run_mod

    fake_today = date(2026, 8, 15)

    class _FakeDT:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 8, 15, 12, 0, 0, tzinfo=tz or timezone.utc)

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
    assert fetch_calls == [("acme/api", date(2026, 7, 1), date(2026, 7, 31), "2026-07")]
    _ = fake_today  # kept for documentation of intent
```

- [ ] **Step 3: Write the failing test-metrics changes**

In `tests/test_metrics.py`:

1. Change the `_load` helper's default `today`:

```python
def _load(fixture: str, today: date = date(2026, 5, 15)):
    cfg = load_config(str(FIXTURES / fixture / "config.yml"))
    return compute(FIXTURES / fixture / "raw", cfg, today=today)
```

2. Update every explicit `today=` argument at the callsites:
   - `_load("multi_month", today=date(2026, 6, 30))` → `_load("multi_month", today=date(2026, 7, 15))` (both existing occurrences — there are three: `test_multi_month_authoring_sums_across_months`, `test_multi_month_truncation_ored_across_months`, `test_multi_month_pr_reviews_deduplicated_by_pr_number`).
   - `_load("missing_month", today=date(2026, 7, 31))` → `_load("missing_month", today=date(2026, 8, 15))`.
   - `_load("partial_error", today=date(2026, 7, 31))` → `_load("partial_error", today=date(2026, 8, 15))`.
   - `_load("partial_error_all_bad", today=date(2026, 7, 31))` → `_load("partial_error_all_bad", today=date(2026, 8, 15))`.

3. In `test_team_share_zero_denominator_is_null` and `test_team_share_pr_reviews_windowed`, both currently pass `today=date(2026, 2, 28)` to `compute(...)`. Change both to `today=date(2026, 3, 15)`.

4. `test_run_metadata_present` currently asserts `out["run"]["until"] == "2026-04-30"`. After Task 2 wires `_effective_end` into `_window_bounds`, with the new `_load` default `today=date(2026, 5, 15)` → `_effective_end = 2026-04-30`, so the assertion is unchanged. **Leave it exactly as-is.**

- [ ] **Step 4: Run all the failing tests to confirm they fail**

Run: `python3 -m pytest -q`
Expected: multiple failures across `test_fetch.py` and `test_metrics.py` — the `_months_between` tests fail because it still uses `today`; the metrics tests fail because `until` values in the JSON no longer match; the `_load` fixture data may be out of window.

- [ ] **Step 5: Update `_months_between`**

In `gh_contributions/fetch.py`, replace the body of `_months_between`:

```python
def _months_between(since: date, today: date) -> list[str]:
    end = _effective_end(today)
    if since > end:
        return []
    out: list[str] = []
    y, m = since.year, since.month
    end_y, end_m = end.year, end.month
    while (y, m) <= (end_y, end_m):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            m = 1
            y += 1
    return out
```

- [ ] **Step 6: Update `_window_bounds` and metrics header**

In `gh_contributions/metrics.py`, add `_effective_end` to the imports from `.fetch`:

```python
from .fetch import _effective_end, _months_between
```

Change the `run.until` line inside `compute(...)`:

```python
    result: dict[str, Any] = {
        "run": {
            "since": config.since.isoformat(),
            "until": _effective_end(today).isoformat(),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "metrics_layers": list(config.metrics),
        },
        "repos": {},
    }
```

Change `_window_bounds`:

```python
def _window_bounds(config: Config, today: date) -> tuple[datetime, datetime]:
    lo = datetime.combine(config.since, time.min, tzinfo=timezone.utc)
    hi = datetime.combine(_effective_end(today), time(23, 59, 59), tzinfo=timezone.utc)
    return lo, hi
```

- [ ] **Step 7: Refine `run.py` empty-months message**

In `gh_contributions/run.py`, locate the existing empty-months branch (currently prints `"since (…) is after today; …"`). Replace with:

```python
    months = _months_between(cfg.since, today)
    if not months or not cfg.repos:
        if not cfg.repos:
            print("no repos configured; writing empty metrics.json", file=sys.stderr)
        elif cfg.since > today:
            print(f"since ({cfg.since}) is after today; writing empty metrics.json", file=sys.stderr)
        else:
            print(f"since ({cfg.since}) is inside the current month; no complete months to fetch yet", file=sys.stderr)
        _write_metrics(run_out, compute(raw_root, cfg, today=today))
        return 0
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python3 -m pytest -q`
Expected: all tests pass (existing 68 + the new/renamed cases).

If any test still fails, inspect its `today=` argument: the rule is that `today` must be strictly in a later calendar month than the newest event date in the fixture. Bump `today` forward.

- [ ] **Step 9: Commit**

```bash
git add gh_contributions/fetch.py gh_contributions/metrics.py gh_contributions/run.py tests/test_fetch.py tests/test_metrics.py
git commit -m "feat: exclude current month from fetch enumeration and metrics window"
```

---

## Task 3: Config and README documentation

**Files:**
- Modify: `config.yml`
- Modify: `README.md`

**Interfaces:** documentation only.

- [ ] **Step 1: Add a comment above `since` in `config.yml`**

In `config.yml` at the repo root (gitignored but locally maintained), replace the current `since: 2026-05-01` line with:

```yaml
# Analysis window starts on this date (UTC) and ends on the last day of the
# previous calendar month. The current month is excluded so we don't cache
# partial data.
since: 2026-05-01
```

Preserve blank lines above and below to match the surrounding style.

- [ ] **Step 2: Update the README sample config**

In `README.md`, replace the sample config block with the same commented form:

```yaml
usernames:
  - alice
  - bob
repos:
  - acme/api
# Analysis window starts on this date (UTC) and ends on the last day of the
# previous calendar month. The current month is excluded so we don't cache
# partial data.
since: 2026-01-01
metrics:
  - authoring
  - collaboration
  - team_share
```

- [ ] **Step 3: Rewrite the Raw-data cache caveat paragraph**

In `README.md`'s `## Raw-data cache` section, replace the final paragraph:

**Before:**

> The current calendar month is **not** auto-refreshed — once its bucket is on disk it stays until you delete it. Delete `out/raw/<current-month>/` (or a single repo inside it) between runs to pick up new activity within the current month.

**After:**

> The current calendar month is never fetched or cached — the analysis window ends on the last day of the previous month (UTC). This keeps every cached bucket a complete, immutable month. To see activity for the current month, wait until it ends.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: current month is excluded from fetching"
```

(`config.yml` is gitignored and won't be staged; that's expected.)

---

## Self-Review Notes

- **Spec coverage** — every section of `docs/superpowers/specs/2026-07-02-exclude-current-month-design.md` maps to a task:
  - `_effective_end` behavior → Task 1.
  - `_months_between`, `_window_bounds`, and `run.py` empty-months message → Task 2.
  - Fixture-test `today=` shifts → Task 2 Step 3.
  - `test_run_skips_complete_buckets_and_fetches_missing` split → Task 2 Step 2.
  - Config + README documentation → Task 3.
- **Type consistency** — `_effective_end(today: date) -> date` used in Task 1, 2. `_months_between(since, today)` signature unchanged (Task 2). `_window_bounds(config, today)` signature unchanged (Task 2).
- **No placeholders** — every step contains the exact code to write or the exact command to run.
- **TDD discipline** — Task 1 writes failing tests first, then implements. Task 2 writes all failing tests (Steps 1–3), confirms they fail (Step 4), then implements (Steps 5–7).
