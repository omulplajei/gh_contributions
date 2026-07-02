# Monthly raw-data cache — design

## Goal

Avoid re-hitting the GitHub API for data that has already been downloaded. Organize raw
API responses on disk by calendar month so that each month, once fetched, can be
reused across runs.

## Summary

- `config.yml` loses `until`. The analysis window is `[since, today (UTC))`.
- Raw JSON is partitioned into `out/raw/<YYYY-MM>/<owner>__<repo>/…`, one bucket per
  (month, repo).
- Each run enumerates months from `since` to the current UTC month and fetches only
  the buckets missing from disk. Complete buckets are skipped with no API calls.
- Metrics computation reads all in-window monthly buckets for a repo and merges them.
  A single combined `metrics.json` and `report.html` are still produced per run,
  under `out/<UTC-timestamp>/`.

## On-disk layout

```
out/
  raw/
    2026-05/
      Flutter-Global__arg/
        _meta.json
        commits.json
        prs_by_created.json
        prs_by_merged.json
        issues_by_created.json
        prs_updated.json
        review_comments.json
        issue_comments.json
        reviews/
          123.json
          124.json
      Flutter-Global__pma/
        …
    2026-06/
      …
    2026-07/
      …
  2026-07-02T183000Z/            # per-run outputs; shape unchanged
    metrics.json
    report.html
```

Each `out/raw/<M>/<owner>__<repo>/` is self-contained. Existing `out/<timestamp>/raw/`
directories from prior runs are left untouched — no migration.

## Config change

`config.yml`:

```yaml
usernames: [...]
repos: [...]
since: 2026-05-01
metrics: [authoring, collaboration, team_share]
```

- `until` is removed.
- If `until` is present in the file, `load_config` raises `ConfigError` with:
  `"'until' has been removed from config; the analysis window now ends at today (UTC). Please remove this key."`
- `since` remains required and is validated as before.
- `metrics.json`'s `run.until` field is populated with today's UTC date at run time.

## Fetch flow

### Month enumeration

- `first_month` = calendar month containing `since` (e.g. `since=2026-05-15` →
  `2026-05`).
- `last_month` = current UTC month.
- Iterate `first_month..last_month` inclusive.
- If `since` is in the future, enumeration yields zero months; an empty
  `metrics.json` is written and a warning is printed (matches today's "no repos"
  path).

### Per-month, per-repo fetch

Loop order: months outer, repos inner. Rationale — keeps API calls for one month
clustered together; an interrupted run leaves whole months either complete or
absent, not half of each.

For each month `M`:

- `month_start` = first day of `M`.
- `month_end` = last day of `M` for past months; `today` for the current month.

For each repo `R` in config:

- If the bucket at `out/raw/<M>/<owner>__<repo>/` is **complete** (see next section),
  print `skip <M> <owner>/<name> (cached)` and continue — zero API calls.
- Otherwise, run the per-repo fetcher against range `[month_start, month_end]` and
  write into that bucket. All eight endpoints are scoped to that range:
  - Search endpoints (`commits`, `prs_by_created`, `prs_by_merged`,
    `issues_by_created`) — the query uses
    `<date-key>:{month_start}..{month_end}` (same shape as the existing
    `committer-date` / `created` / `merged` filters in `fetch.py`, just with
    month-scoped bounds).
  - `prs_updated` — walks `updated_at desc`, stops when
    `updated < month_start`; skips items with `updated > month_end`.
  - `review_comments` / `issue_comments` — walk `created desc`, stop when
    `created < month_start`; skip items with `created > month_end`.
  - `reviews/<pr>.json` — for every PR in that month's `prs_updated.json`; the
    full review history for each PR is stored (window filtering happens later in
    metrics). A PR touched in multiple months therefore has its reviews stored
    once per month; contents are identical.

### Completion detection

A `(month, repo)` bucket is **complete** iff
`out/raw/<M>/<owner>__<repo>/_meta.json` exists, parses as JSON, and does not
contain an `error` key.

This reuses the current convention: `_meta.json` is written last on success, so
its presence implies all endpoint JSON files were written first. Any crash
mid-fetch leaves `_meta.json` absent → next run re-fetches from scratch. Error
paths (`_write_error` for `NotFoundError` / `RateLimitError`) wipe the bucket
and write `_meta.json` with `{"error": "..."}` → not complete → next run
retries.

**Empty-but-valid months** — a month with no activity still writes `_meta.json`
with per-endpoint counts of zero and `truncated: false`. Considered complete
(same as today's `empty_repo` fixture).

### Current-month caveat

The current calendar month is treated no differently from past months: once its
bucket is written and marked complete, subsequent runs during the same month
will not re-fetch it. To pick up new activity within the current month, the
user deletes that month's directory (per-repo or whole-month) manually. Documented
in README.

## Metrics computation

`compute(raw_dir, config)` in `metrics.py` today walks a single directory per
repo. It changes to walk one directory per (month, repo) and merge across
months.

### Signature and month list

- `compute(raw_root, config)` where `raw_root = out/raw/`.
- Month list = the same `first_month..last_month` enumeration used by fetch.
- For each repo, `_compute_repo` gathers the per-month bucket directories that
  exist and are complete, then computes over the merged data.

### Merging strategy

Two small helpers keep per-metric logic unchanged:

```python
_load_endpoint(raw_root, months, owner, name, "commits.json") -> list[dict]
_load_reviews(raw_root, months, owner, name)                  -> dict[int, list[dict]]
```

- `_load_endpoint` concatenates the JSON arrays across all in-window monthly
  buckets for that repo (skipping missing or errored buckets).
- `_load_reviews` merges `reviews/<pr>.json` files across months, keyed by PR
  number. Duplicate PRs across months take last-writer-wins — safe because the
  same PR fetched in two different months returns the same review history.

`_apply_authoring`, `_apply_collaboration`, `_apply_team_share` are refactored
to receive already-merged lists / dicts instead of a `repo_dir` path. Their
per-item logic (window filter with `_in_window`, author-login extraction,
comment-parent detection) is unchanged.

### Truncation flags

Per-month `_meta.json` still carries per-endpoint `{total_count, truncated}`.
In the merged output, an endpoint is reported truncated if **any** month bucket
for that endpoint is truncated (OR across months). Surfaced under
`repos[<repo>].truncated`, same key names as today.

### Per-repo error surfacing

Today, a single errored `_meta.json` produces
`{per_user: None, team_share: None, truncated: None, error: "..."}` for the
repo. With months, partial success is possible.

- If **all** attempted months for that repo errored or are missing →
  `error = "<summarized reason>"`, no per-user data (same shape as today).
- If **some** months succeeded → per-user counts are computed from the
  successful months, and `error` becomes a structured note like
  `"partial: failed months: 2026-07 (rate_limited)"`. `truncated` reflects only
  the successful months.

### Report

`report.html` generation from `metrics.json` is unchanged. The schema keys
(`per_user`, `team_share`, `truncated`, `error`) stay the same.

## Error handling & edge cases

- **Auth failure (401)** — `AuthError` from `github_client` aborts the whole run
  with exit code 2. No `_meta.json` is written for the in-flight bucket, so the
  next run retries after the token is fixed.
- **Rate limit exhausted (403 / 429 retry limit)** — per-(month, repo)
  `_meta.json` gets `{"error": "rate_limited: ..."}`. The month-outer loop
  continues to later months. Cached months from prior runs still produce a
  useful report.
- **Repo not found (404)** — per-(month, repo) `_meta.json` gets
  `{"error": "not_found"}` for every month attempted. Metrics treats this as a
  fully-errored repo.
- **Interrupted run (Ctrl-C, crash)** — in-flight bucket lacks `_meta.json`
  → re-fetched on next run. Earlier completed buckets are preserved.
- **`since` in the future or same day as today** — enumeration yields at most
  the current month; may yield zero. Empty `metrics.json` + warning.
- **Empty months** — valid `_meta.json` with zero counts. Considered complete.
- **Out-of-window cached buckets** — if `since` moves forward between runs
  (e.g. previously `2026-01-01`, now `2026-05-01`), the older month buckets
  under `out/raw/` are simply ignored by fetch and metrics. They are neither
  read nor deleted; the user can remove them manually.
- **Time zone** — "current UTC month" and "today (UTC)" everywhere.
  `_window_bounds` in metrics keeps its existing UTC handling.
- **Concurrent runs** — out of scope. Two parallel runs could race on the same
  bucket; documented as unsupported. No file locking.

## Testing

Fixtures live under `tests/fixtures/`. Each is a directory with `config.yml`
and a `raw/` tree. Tests point `compute()` at these trees; no live API calls.

### Fixture migration

Existing fixtures (`authoring/`, `collaboration/`, `team_share/`, `empty_repo/`,
`truncated/`) currently have `raw/<owner>__<repo>/…`. They are migrated by
moving each repo tree under a single-month folder (e.g.
`raw/2026-06/<owner>__<repo>/`) and updating the fixture `config.yml` so that
`since` falls within that month. `until` is dropped from every fixture config.

### New fixtures

- `multi_month/` — a repo with buckets under `raw/2026-05/…` and
  `raw/2026-06/…`, each with a few commits, PRs, reviews. Verifies:
  - Metric counts sum correctly across months.
  - Truncation OR-across-months (May truncated commits, June not → merged
    output reports truncated).
  - The same PR having `reviews/<n>.json` in both months is not double-counted.
- `partial_error/` — May and June complete, July `_meta.json` has an error.
  Verifies the `partial: failed months: 2026-07 (...)` reporting path.
- `missing_month/` — May and July present, June absent. Verifies gaps in the
  middle contribute zero without turning the repo into an error.

### New / updated unit tests

- `test_config.py` — `until` present in config raises `ConfigError` with the
  documented message; `since` remains required and validated.
- `test_fetch.py` (new file) — month enumeration helper
  (`_months_between(since, today)`): boundaries, `since` in the future, `since`
  in the current month. Also: skip-if-complete logic against a pre-existing
  `_meta.json` on disk, asserted via a mocked `GitHubClient` call counter.
- `test_metrics.py` — cases for each new fixture above; existing cases updated
  to the new fixture layout.
- `test_report.py` — no changes expected.

### Live-fetch tests

Out of scope. The fetcher's HTTP path is not exercised end-to-end; skip and
loop logic use a mocked client.

## Documentation

README gains a short section that covers:

- The monthly cache under `out/raw/<YYYY-MM>/…`.
- Removal of `until` from `config.yml`.
- How to force a refresh: delete the relevant month directory (or a specific
  `<month>/<owner>__<repo>/` bucket) and re-run.
- The current-month staleness caveat.
