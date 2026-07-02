# Exclude current month from GitHub data fetching — Design

## Motivation

The monthly raw-data cache currently enumerates months from `since` through today, clamping the current-month bucket's upper bound to today via `_month_bounds`. This caches a partial bucket for the in-progress month: subsequent runs treat it as complete and never re-fetch it, so any activity later in that month is silently missed. We want the cache to only ever contain complete calendar months.

## Goal

Never fetch or cache raw data for the current calendar month. The analysis window ends at the last day of the previous calendar month (UTC).

## Non-goals

- No changes to fetcher HTTP behavior, pagination, or auth handling.
- No changes to `metrics.json` schema keys.
- No mechanism to opt back in to the current month.

## Approach

Introduce a single helper `_effective_end(today: date) -> date` that returns the last day of the previous calendar month. Two call sites use it:

- `gh_contributions.fetch._months_between(since, today)` — enumerates months up to `_effective_end(today)` instead of `today`.
- `gh_contributions.metrics._window_bounds(config, today)` — the metrics window `until` becomes `_effective_end(today)`.

Because `_months_between` is the single source of month enumeration for both the fetch loop (`run.py`) and the metrics walker (`metrics.py`), one change here excludes the current month everywhere consistently. `_month_bounds`'s "clamp to today" branch is kept as defensive code but becomes unreachable in the normal path.

## Behavior

### `_effective_end(today)`

- Returns `date(today.year, today.month, 1) - timedelta(days=1)`.
- Jan 1 example: `_effective_end(date(2026, 1, 1))` = `date(2025, 12, 31)`.

### `_months_between(since, today)`

- Replaces the existing `if since > today: return []` guard with `if since > _effective_end(today): return []`. Because `_effective_end(today) < today` always, this subsumes the old guard and additionally covers the case where `since` is within the current calendar month.
- Otherwise enumerates from `since`'s month through `_effective_end(today)`'s month, inclusive.

### `_window_bounds(config, today)`

- Returns `(datetime(since, 00:00 UTC), datetime(_effective_end(today), 23:59:59.999999 UTC))`.
- `metrics.json`'s `run.until` becomes the last day of the previous calendar month.

### `run.py`

- When `_months_between(...)` returns `[]` and `since` is inside the current month, log:
  `"since (<since>) is inside the current month; no complete months to fetch yet"` and write empty `metrics.json`, exit 0. (Reuses today's short-circuit for `since > today`; message text differentiates the case.)
- Otherwise unchanged: iterate months, skip complete buckets, fetch missing, then compute + write metrics.

## Config and documentation

`config.yml` (repo root, gitignored) and the README sample config get a comment above `since`:

```yaml
# Analysis window starts on this date (UTC) and ends on the last day of the
# previous calendar month. The current month is excluded so we don't cache
# partial data.
since: 2026-05-01
```

The README "Raw-data cache" section's final paragraph — currently a caveat that the current month is not auto-refreshed and must be manually deleted — is rewritten to state that the current month is never fetched or cached; to see activity for the current month, wait until it ends.

## Edge cases

| Case | Behavior |
| --- | --- |
| `since` is in a past month, today is any date | Enumerate `[since_month .. previous_month]`. |
| `since` is in the current month | `_months_between` returns `[]`. `run.py` writes empty metrics.json, exits 0. |
| `since` is in the future | Same as today's `since > today` path: empty metrics.json, exit 0. |
| Today is Jan 1 | Previous month is Dec of prior year. Enumeration and metrics window handle year rollover correctly. |
| A stale current-month bucket exists on disk (from before this change) | Enumeration never lists the current month, so the bucket is neither read nor refreshed. It's harmless clutter; document that the user can delete it. |

## Testing

New `tests/test_fetch.py` cases:

- `_effective_end` returns the last day of previous month, including the Jan-1 → Dec-31 rollover.
- `_months_between` excludes the current month when today is mid-month.
- `_months_between` returns `[]` when `since` is inside the current calendar month.

Fixture-based tests: every call to `_load(fixture, today=...)` and every `compute(..., today=...)` must set `today` to a date at least one month after the fixture's newest datapoint, because the window now ends at `_effective_end(today)`. Concrete shifts:

- `_load` default: `date(2026, 4, 30)` → `date(2026, 5, 15)`.
- `multi_month`: `date(2026, 6, 30)` → `date(2026, 7, 15)`.
- `missing_month`: `date(2026, 7, 31)` → `date(2026, 8, 15)`.
- `partial_error` and `partial_error_all_bad`: `date(2026, 7, 31)` → `date(2026, 8, 15)`.
- `test_team_share_zero_denominator_is_null` and `test_team_share_pr_reviews_windowed`: `date(2026, 2, 28)` → `date(2026, 3, 15)`.

`test_run_metadata_present` currently asserts `until == "2026-04-30"`. After the `_load` default shifts to `date(2026, 5, 15)`, `_effective_end` still yields `date(2026, 4, 30)`, so the assertion continues to hold as-is.

`test_run_skips_complete_buckets_and_fetches_missing`:

- Set `fake_today = date(2026, 8, 15)` so enumeration = `[May, Jun, Jul]`.
- Split into two tests:
  - `test_run_skips_all_complete_buckets`: pre-populate May+June+July as complete; assert zero fetch calls, rc == 0.
  - `test_run_fetches_missing_month`: pre-populate May+June only; assert single fetch call for July (`("acme/api", date(2026, 7, 1), date(2026, 7, 31), "2026-07")`).

## Files changed

- `gh_contributions/fetch.py` — add `_effective_end`; update `_months_between`.
- `gh_contributions/metrics.py` — use `_effective_end` in `_window_bounds`.
- `gh_contributions/run.py` — adjust the empty-months log message.
- `config.yml` — add comment above `since`.
- `README.md` — sample-config comment; rewrite Raw-data cache caveat.
- `tests/test_fetch.py` — new `_effective_end` and updated `_months_between` tests; reshape `test_run_*`.
- `tests/test_metrics.py` — bump every `today=` argument by one month.
