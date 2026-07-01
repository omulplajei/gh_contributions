# HTML Report — Design

**Date:** 2026-07-01
**Status:** Approved
**Scope:** Add a single self-contained HTML report renderer that turns `out/<run>/metrics.json` into `out/<run>/report.html`, with an aggregate-across-repos view and a per-repo view for both team-share and per-user comparisons.

## Goal

Give the team lead a single file they can open in a browser to answer two questions in one place:

1. How much of each repo's activity comes from the team (team-share), aggregated across all repos and broken down per repo?
2. How does each team member compare on authoring, reviewing, and commenting — aggregated and per repo?

The file must be shareable (email, Slack, shared drive) and work offline.

## Non-Goals

- No server, no dashboard, no live queries. Report is static HTML generated once per run.
- No cross-run trend charts. One report per run; comparing runs is out of scope.
- No display-name mapping. Users are identified by GitHub login.
- No visual-regression tests. Manual inspection on first render.
- No modification to fetching, config loading, or `github_client`.

## Architecture

New module `gh_contributions/report.py` with a pure `render(metrics) -> str` and a thin `main(argv) -> int` CLI wrapper. No new runtime dependencies. Chart.js is vendored into the repo at `gh_contributions/assets/chart.umd.min.js` (committed once) and inlined into every rendered HTML file so the output is a single self-contained artifact.

Existing modules:

- `metrics.py` gets one small extension: `team_share` values become `{team: int, total: int, share: float | None}` instead of bare floats, so aggregate ratios can be re-derived correctly at report time.
- `run.py`, `fetch.py`, `github_client.py`, `config.py` — unchanged.

### Module boundaries

| Module | Purpose | Depends on |
|---|---|---|
| `gh_contributions/report.py` | Read `metrics.json`, synthesize aggregate view, render HTML | stdlib only |
| `gh_contributions/assets/chart.umd.min.js` | Vendored Chart.js UMD bundle (~210 KB minified) | none — static asset |

### CLI

```
python3 -m gh_contributions.report [<run-dir>]
```

- `<run-dir>` optional. Defaults to the newest directory under `out/` (lexicographic sort — safe because run ids are UTC timestamps `YYYY-MM-DDTHHMMSSZ`).
- Reads `<run-dir>/metrics.json`, writes `<run-dir>/report.html`.
- Prints the output path to stderr on success.
- Exit codes:
  - `0` — report written.
  - `2` — run dir missing, `metrics.json` missing, or `metrics.json` malformed.

### Internal shape of `report.py`

- `render(metrics: dict) -> str` — pure. Takes the on-disk `metrics.json` structure (with the new team_share shape), returns the full HTML string.
- `_aggregate(metrics: dict) -> dict | None` — pure. Returns a synthetic repo entry with the same shape as any real repo, keyed as `"__aggregate__"`. Returns `None` when all repos errored.
- `main(argv: list[str] | None = None) -> int` — resolves the run dir, reads the file, calls `render`, writes `report.html`.

`render` receives the metrics dict with `__aggregate__` already spliced in by `main`, so the rendering code has no branch for "is this aggregate or a real repo?".

## Data Flow

```
out/<run>/metrics.json
   │
   ▼
main() reads and parses
   │
   ▼
_aggregate(metrics) → adds metrics["repos"]["__aggregate__"]
   │
   ▼
render(metrics) → HTML string
   │
   ▼
out/<run>/report.html written
```

### Aggregation rules

- **`per_user`:** for each user, sum every leaf integer in `authoring` (`commits`, `pull_requests_opened`, `pull_requests_merged`, `issues_opened`) and `collaboration` (each `reviews_given` state, `review_comments`, `pr_conversation_comments`, `issue_comments`, `cross_team_reviews`) across all repos where the user appears. Skip any repo whose `error` is set.
- **`team_share`:** ratios are not additive — recomputed. Sum `team` and `total` separately across all non-errored repos; the aggregate share is `sum(team) / sum(total)`, or `None` if `sum(total) == 0`. Requires the new `{team, total, share}` shape in each repo's `team_share`.
- **`truncated`:** union — if any non-errored repo has `truncated.<endpoint>: true`, the aggregate carries `truncated.<endpoint>: true`.
- **`error`:** always `None` on the aggregate itself. If all repos errored, `_aggregate` returns `None`; the aggregate section shows a "No data — all repos failed" banner.

### `metrics.py` change

The existing `_apply_team_share` returns:

```json
"team_share": {
  "share_commits": 0.213,
  "share_pull_requests_opened": 0.138,
  "share_reviews_given": 0.141,
  "share_comments": 0.097
}
```

Change to:

```json
"team_share": {
  "commits":              {"team": 56, "total": 262, "share": 0.213},
  "pull_requests_opened": {"team": 4,  "total": 29,  "share": 0.138},
  "reviews_given":        {"team": 62, "total": 440, "share": 0.141},
  "comments":             {"team": 65, "total": 670, "share": 0.097}
}
```

Zero-denominator entries: `{"team": 0, "total": 0, "share": null}`.

This is a breaking change to `metrics.json`'s shape. There are no other consumers today. Existing `out/` runs will be deleted before the change lands (see plan); users re-fetch to produce the new shape.

## Charts & Layout

Each tab renders exactly four charts in a 2×2 grid plus one details table below. The same layout is used for the aggregate tab and every per-repo tab.

- **Chart 1 — Team share (grouped bar, 4 bars):** `share_commits`, `share_pull_requests_opened`, `share_reviews_given`, `share_comments`. Y-axis 0–1 formatted as percent. Tooltip shows raw `team / total`.
- **Chart 2 — Authoring per user (grouped bar):** for each user, four bars — commits, PRs opened, PRs merged, issues opened. Sorted descending by commits.
- **Chart 3 — Reviews given per user (stacked bar):** stack of `APPROVED`, `CHANGES_REQUESTED`, `COMMENTED`. Sorted descending by total reviews.
- **Chart 4 — Comments per user (stacked bar):** stack of `review_comments`, `pr_conversation_comments`, `issue_comments`. Sorted descending by total comments.
- **Details table:** one row per user with every raw count from `authoring` + `collaboration`. Column-header click sorts ascending/descending (small vanilla-JS handler in the page).

Users identified by GitHub login. Non-team logins and bots are already absent from `per_user` — no extra filtering in the report.

### Page layout

```
┌── Header: run window, repos in scope, generated_at
├── Truncation banner (yellow) — shown iff any endpoint in any repo was truncated
├── Tabs: [All repos] [Flutter-Global/arg] [orgX/repoY] ...   (All repos always first)
└── Active tab body:
    ┌──────────────────────┬──────────────────────┐
    │ Chart 1 team_share   │ Chart 2 authoring    │
    ├──────────────────────┼──────────────────────┤
    │ Chart 3 reviews      │ Chart 4 comments     │
    └──────────────────────┴──────────────────────┘
    [ details table ]
```

### HTML shape

The rendered HTML is:

```html
<!doctype html>
<html>
  <head>… inline CSS …</head>
  <body>
    <header>…</header>
    <div id="banners">…</div>
    <nav id="tabs">…</nav>
    <main id="tab-bodies">
      <section data-repo="__aggregate__">… 4 <canvas> + <table> …</section>
      <section data-repo="Flutter-Global/arg" hidden>…</section>
      …
    </main>
    <script id="report-data" type="application/json">
      { "aggregate": {…chart datasets…},
        "repos":     {"Flutter-Global/arg": {…}, …},
        "run":       {…} }
    </script>
    <script>/* vendored Chart.js UMD bundle */</script>
    <script>/* ~50 lines: tab switching, table sorting, Chart.js instantiation from #report-data */</script>
  </body>
</html>
```

Chart datasets are pre-computed by `render` (Python) and embedded as JSON; the inline script only reads them and calls `new Chart(...)`. This keeps the JS trivial and makes the primary test assertion "parse the embedded JSON and compare".

## Error & Edge Handling

Handled in `render` (pure) unless noted otherwise.

- **Missing / malformed `metrics.json`:** `main` prints `report error: <reason>` to stderr, exits `2`.
- **Empty `out/`** (no run dirs): `main` prints `no run directories found under out/`, exits `2`.
- **No repos in the run** (`metrics["repos"] == {}`): valid HTML with header + a single "No repos in this run" panel. No tabs, no charts. Exit `0`.
- **All repos errored:** aggregate tab shows red "No data — all repos failed" banner listing repo names + error strings. Per-repo tabs still present, each showing its own error banner in place of the 2×2 grid.
- **One repo errored:** that repo's tab shows its error banner in place of the 2×2 grid. Aggregate skips it silently.
- **Truncation:** yellow banner at page top when any endpoint in any repo hit the 1000-item cap. Body lists `<repo>/<endpoint>` pairs. Individual affected tabs carry the same banner. Ratios and totals still shown — the banner is the disclosure.
- **Zero-denominator team_share:** rendered as "—" in tooltip; bar drawn at height 0 with hover note "no data in window".
- **Layer disabled in config** (e.g., `metrics: [authoring]` only): charts fed by absent layers render a "layer disabled in config" placeholder card instead of the chart, keeping the 2×2 shape. Aggregate follows the union of enabled layers.

No new exception types are introduced.

## Testing

Consistent with the existing project split: pure logic is TDD'd, glue is smoke-tested. No visual-regression tests.

### `tests/test_report.py`

**`_aggregate` (4 tests):**

1. Sums per-user counts across two repos correctly.
2. Re-derives team_share ratios: aggregate share equals `sum(team) / sum(total)`, not the mean of per-repo shares.
3. Unions truncation flags — if any non-errored repo has `truncated.commits: true`, aggregate does too.
4. Skips errored repos; returns `None` when all repos errored.

**`render` (6 tests):**

Assertions target embedded JSON payloads and DOM markers, not rendered visual output.

1. Given a metrics dict, the returned string contains a `<script id="report-data" type="application/json">…</script>` block whose parsed content matches the expected chart datasets (aggregate + per-repo, in that order).
2. One tab button per repo plus one for `__aggregate__`. Order: `__aggregate__` first, then real repos in the order they appear in `metrics["repos"]` (which is `config.repos` order — dict insertion order is preserved by both `json.load` and Python dicts).
3. Truncation banner present iff any repo has a truncated endpoint.
4. Per-tab error banner present for each errored repo; not present for healthy repos.
5. "Layer disabled" placeholder appears when a layer is absent from `run.metrics_layers`; absent otherwise.
6. Empty-repos payload renders the "no repos" panel and zero tabs.

**`main` (3 smoke tests, use `tmp_path`):**

1. Writes a synthetic `metrics.json` under `tmp_path`; `main([str(tmp_path)])` returns `0`; `report.html` exists; contains the vendored Chart.js bytes (byte-length check, no JS assertions).
2. Returns `2` when `<run-dir>/metrics.json` is missing.
3. Given `tmp_path/out/2026-01-01T000000Z/` and `tmp_path/out/2026-02-01T000000Z/` both containing valid `metrics.json`, `main([])` with `cwd=tmp_path` picks the newer of the two.

### `tests/test_metrics.py` changes

The two team_share tests are updated (not added) to the new `{team, total, share}` shape:

- `test_team_share_happy_path` — assert `team`/`total`/`share` per bucket instead of bare share.
- `test_team_share_zero_denominator_is_null` — assert `team == 0`, `total == 0`, `share is None`.

The team_share fixture's expected values change accordingly; no fixture file edits, only test assertions.

### Chart.js

Not tested. Vendored third-party bundle. The byte-length check in `main` test #1 is the only assertion.

### Total suite delta

+10 new tests, ~2 modified. Suite goes from 21 → 31.

## Success Criteria

1. `python3 -m pytest tests/ -q` — 31 passed.
2. After a real `run.py` invocation against `Flutter-Global/arg`, `python3 -m gh_contributions.report` produces `out/<run>/report.html`.
3. Opening `report.html` in a browser with no internet connection renders four charts on the aggregate tab and one tab per configured repo.
4. `python3 -m gh_contributions.report /nope` exits `2`.
5. `git status --porcelain` is clean; the generated `report.html` is git-ignored (it lives under `out/`, which is already ignored).
