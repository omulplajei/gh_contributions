# Team share pie charts — design

Status: draft
Date: 2026-07-02

## Goal

Replace the single 4-bar team-share chart at the top of each repo tab in the
HTML report with a row of **three doughnut charts** whose layers mirror the
per-user activity chart directly below:

1. Commits — team share of total commits
2. PR activity — team share of (opened + merged + reviews given)
3. Comments — team share of (review + PR-conv + issue comments)

Two-slice doughnuts make "how much of this activity was ours?" more explicit
than a bar with a 0–1 axis, and matching the three per-user layers lets the
eye read top-to-bottom: team-vs-rest at the top, then per-team-member
breakdown by the same three layers below.

## Scope

In scope:

- `gh_contributions/metrics.py` — extend `_apply_team_share` to emit a
  per-sub-metric team/total shape covering the three layers, including
  merged PRs and the PR-conv/issue-comment split.
- `gh_contributions/report.py` — update `_aggregate` for the new shape;
  update `_chart_data` to emit a `team_share` block matching per-user
  `activity` conventions; update `_tab_body` to render one row of three pie
  cells instead of one team-share cell; add doughnut chart rendering + pie
  tooltip in `_APP_JS`.
- CSS: replace the compact `.cell-team-share` styling with a
  three-pie-row layout.
- Unit tests for the new metric shape, aggregation, and chart-data emission.
- Fixture: add `prs_by_merged.json` to `tests/fixtures/team_share/raw/acme__api/`.

Out of scope:

- Any change to fetching (`gh_contributions/fetch.py` already fetches
  `prs_by_merged.json` unconditionally).
- Any change to `authoring` / `collaboration` layers, the per-user activity
  chart, or the details table.
- Cross-run trends, display-name mapping, alternate chart-type toggles.
- JS unit tests (no JS test infrastructure exists today).
- Chart.js version bump; asset changes.

## Layer definitions

Layer sub-metrics match the per-user activity chart 1:1:

| Layer      | Sub-metrics summed                                                                                                                                                                                     |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `commits`  | `commits`                                                                                                                                                                                              |
| `pr`       | `pull_requests_opened` + `pull_requests_merged` + reviews_given `APPROVED` + `CHANGES_REQUESTED` + `COMMENTED`                                                                                          |
| `comments` | `review_comments` + `pr_conversation_comments` + `issue_comments`                                                                                                                                       |

The two per-user metrics that are excluded from every per-user activity
layer (`issues_opened`, `cross_team_reviews`) are also excluded from every
team_share layer, for consistency.

## Metric shape (metrics.json)

`team_share` changes from four flat buckets to three nested layer buckets:

```json
"team_share": {
  "commits": {
    "team":  {"commits": 56},
    "total": {"commits": 262},
    "share": 0.213
  },
  "pr": {
    "team": {
      "pull_requests_opened":  4,
      "pull_requests_merged":  3,
      "APPROVED":             12,
      "CHANGES_REQUESTED":     2,
      "COMMENTED":            45
    },
    "total": {
      "pull_requests_opened":  29,
      "pull_requests_merged":  22,
      "APPROVED":              80,
      "CHANGES_REQUESTED":     40,
      "COMMENTED":            320
    },
    "share": 0.134
  },
  "comments": {
    "team": {
      "review_comments":           8,
      "pr_conversation_comments": 22,
      "issue_comments":           35
    },
    "total": {
      "review_comments":          80,
      "pr_conversation_comments": 210,
      "issue_comments":           380
    },
    "share": 0.097
  }
}
```

Invariants (per layer):

- `share == sum(team.values()) / sum(total.values())`, or `null` when
  `sum(total.values()) == 0`.
- Sub-metric keys within each layer are fixed (as listed in the Layer
  definitions table) and always present, even at value 0. Raw sub-metric
  names match the per-user `activity.breakdown` payload; display renaming
  (e.g. `pull_requests_opened → "opened"`) stays a pure JS concern.
- Layer keys are exactly `commits`, `pr`, `comments` — old flat keys
  (`pull_requests_opened`, `reviews_given`, flat `comments`) are gone.

This is a breaking change to `metrics.json`. Existing `out/` runs must be
regenerated. There are no other consumers of `metrics.json` today.

## `_apply_team_share` behavior

Data sources per layer:

- **Commits.** Reads `commits.json` (already window-filtered by search
  query). For each commit, look up `author.login`; increment `total.commits`
  always, increment `team.commits` when login is in the team set.
- **PR layer.**
  - `pull_requests_opened`: reads `prs_by_created.json` (window-filtered).
    Login lookup via `user.login`.
  - `pull_requests_merged`: reads `prs_by_merged.json` (window-filtered).
    Login lookup via `user.login`.
  - `APPROVED` / `CHANGES_REQUESTED` / `COMMENTED`: iterates
    `reviews/*.json` files, filters to the three counted states and to
    reviews inside the `since..until` window (same rule as
    `_apply_collaboration`). Login lookup via `user.login`.
- **Comments layer.**
  - `review_comments`: reads `review_comments.json`, window-filtered.
    Login lookup via `user.login`.
  - `pr_conversation_comments` / `issue_comments`: reads
    `issue_comments.json`, window-filtered. Split by `_parent_number(issue_url)`
    against the set of PR numbers in `prs_updated.json` — same logic as
    `_apply_collaboration`, but aggregated to team-vs-total instead of
    per-user.

For each sub-metric the function accumulates two integer counts: `total`
(everyone) and `team` (login in the team set). `share` is computed once at
the end from the summed team and total totals.

## Aggregation (`_aggregate` in report.py)

Same rule as today, applied to the new shape:

- Sum every `team.<sub>` and `total.<sub>` across all non-errored repos.
- Recompute `share` from the summed sub-totals (ratios are not additive).
- Empty-corpus repos contribute nothing and don't affect aggregate share.
- Truncation flags: unchanged (union of per-repo truncation flags).

## Rendering

### `_chart_data` payload

The current per-repo `team_share` chart-data block is replaced by:

```json
"team_share": {
  "layers":   ["commits", "pr", "comments"],
  "shares":   [0.213, 0.134, 0.097],
  "team":     [56, 66, 65],
  "total":    [262, 491, 670],
  "breakdown": {
    "commits":  {
      "team":  {"commits": 56},
      "total": {"commits": 262}
    },
    "pr": {
      "team":  {"pull_requests_opened": 4, "pull_requests_merged": 3,
                "APPROVED": 12, "CHANGES_REQUESTED": 2, "COMMENTED": 45},
      "total": {"pull_requests_opened": 29, "pull_requests_merged": 22,
                "APPROVED": 80, "CHANGES_REQUESTED": 40, "COMMENTED": 320}
    },
    "comments": {
      "team":  {"review_comments": 8, "pr_conversation_comments": 22,
                "issue_comments": 35},
      "total": {"review_comments": 80, "pr_conversation_comments": 210,
                "issue_comments": 380}
    }
  }
}
```

Invariants:

- `team[i] + non_team[i] == total[i]`; non-team is derived in JS as
  `total[i] - team[i]`.
- Layer order is fixed: `commits`, `pr`, `comments` — matches the per-user
  `activity` dataset order (and therefore the palette color assignment).
- `shares[i]` is `null` when `total[i] == 0`.

### Layout

`_tab_body` moves from a two-cell stack to a three-cell stack:

```
+---------------------------------------------------------------+
|  Team share row (3 doughnuts, side-by-side, wrap on narrow)   |
|  [Commits]         [PR activity]        [Comments]            |
+---------------------------------------------------------------+
|  Activity (per-user horizontal stacked bar, full width)       |
+---------------------------------------------------------------+
|  Details table  (unchanged)                                   |
+---------------------------------------------------------------+
```

CSS changes in `_CSS`:

- Replace `.cell-team-share { max-width: 480px; }` and
  `.cell-team-share canvas { max-height: 320px; }` with:
  - `.team-share-row { display: flex; flex-direction: row; flex-wrap: wrap; gap: 16px; }`
  - `.cell-pie { flex: 1 1 240px; max-width: 320px; }`
  - `.cell-pie canvas { max-height: 260px; }`
- The `.stack` vertical flex is unchanged; the team-share row is one item
  in that stack, followed by the activity cell, followed by the table.

### `_tab_body` change

Emits, in order:

1. Team-share row (new small helper `_team_share_row(repo_name, layers)`)
   containing three `.cell-pie` divs, each with a canvas keyed
   `data-chart="team_share"` and `data-layer` set to `commits` / `pr` /
   `comments`. If the `team_share` layer is disabled in config, the entire
   row is replaced by the existing "layer disabled in config" placeholder
   card, spanning the width of the row.
2. Activity cell (`_cell('activity', ...)`), unchanged.
3. Details table, unchanged.

### JS chart config (per pie)

In `_APP_JS`, add a new branch for `kind === 'team_share'`. For each canvas
with `data-chart="team_share"`, read `data-layer` to get the layer name and
its index into the payload arrays:

```js
new Chart(canvas, {
  type: 'doughnut',
  data: {
    labels: ['Team', 'Non-team'],
    datasets: [{
      data: [team[i], total[i] - team[i]],
      backgroundColor: [palette[i], '#d1d5db'],
      borderWidth: 1,
    }],
  },
  options: {
    plugins: {
      title:  { display: true, text: layerTitle[i] + ' \u2014 ' + sharePercent(i) },
      legend: { position: 'bottom' },
      tooltip: { callbacks: { label: pieTooltipLabel } },
    },
  },
});
```

`layerTitle` map: `{ commits: 'Commits', pr: 'PR activity', comments: 'Comments' }`
— same labels as the per-user chart's dataset labels.

`sharePercent(i)` returns `"21.3%"` when `shares[i]` is a number, `"no data"`
when it's `null`.

**Tooltip parity with per-user chart.** `pieTooltipLabel(ctx)` builds:

```
{sliceLabel}: {sliceCount} / {total}  (sub1 X, sub2 Y, ...)
```

using the same `displayNames` map that already lives in `_APP_JS` for the
per-user tooltip. Rules copied verbatim from the per-user tooltip:

- Parenthetical omitted when the slice count is 0.
- Parenthetical omitted when the layer has a single sub-metric (i.e. the
  Commits layer).
- Non-team sub-metric counts are `total.<sub> - team.<sub>`, computed
  inline in the tooltip callback from the layer's `breakdown` entry.

### Colors

Team slice uses the layer's per-user palette color; non-team slice uses a
neutral grey.

- Commits: `palette[0]` (blue) team / `#d1d5db` non-team
- PR: `palette[1]` (green) team / `#d1d5db` non-team
- Comments: `palette[2]` (yellow) team / `#d1d5db` non-team

## Edge cases

- **Zero-denominator layer** (`total[i] == 0`): pie cell is replaced by a
  small placeholder card with the layer title and text "no data in window".
  No empty doughnut is rendered — an empty ring is ambiguous. `shares[i]`
  is `null` in the payload; JS checks for null and swaps in the placeholder.
- **Zero-team, non-zero-total** (team contributed nothing in a layer):
  doughnut renders normally with a full grey ring, `0.0%` in the title, and
  tooltip on the team slice showing `Team: 0 / N` with no parenthetical
  (0-count rule).
- **`team_share` layer disabled in config**: entire team-share row is
  replaced by the existing "layer disabled in config" placeholder card,
  spanning the width of the row.
- **Repo error**: `team_share` block absent from `_chart_data` output;
  aggregate skips the repo; repo tab shows the existing error banner in
  place of both the team-share row and the activity chart.
- **All repos errored**: `_aggregate` returns `None`; aggregate tab shows
  the existing "No data — all repos failed" banner. No pies rendered.
- **Truncation banners**: unchanged. The four search endpoints
  (`commits`, `prs_by_created`, `prs_by_merged`, `issues_by_created`) that
  can hit the 1000-cap still surface in the top-of-page and per-tab
  banners.
- **Aggregate tab**: `_aggregate` produces the same `team_share` shape as
  any real repo, so `_chart_data` and the JS renderer are identical for
  the aggregate tab. No special-casing.
- **Config layer combinations**: when `authoring`/`collaboration` are
  disabled but `team_share` is enabled, the team-share pies still render
  (they read raw files directly, not per-user data). No note is added on
  the pies for missing per-user layers — the per-user chart's existing
  layer-disabled note already covers that story.

## Testing

Consistent with today's split: pure Python is TDD'd, JS glue is
smoke-tested manually.

### `tests/test_metrics.py` — new / updated for `_apply_team_share`

1. **Commits layer.** Fixture with 5 commits (3 by team, 2 non-team) →
   `team_share.commits.team == {"commits": 3}`,
   `total == {"commits": 5}`, `share == 0.6`.
2. **PR layer sub-metric sums.** Fixture with known opened/merged PRs and
   reviews across all three states, some by team, some not → assert every
   sub-metric count in both `team` and `total`, and
   `share == sum(team.values()) / sum(total.values())`.
3. **PR layer window filtering.** Reviews outside `since..until` excluded
   from both `team` and `total` (same rule that `_apply_collaboration`
   already applies).
4. **Comments layer PR-conv/issue split.** An issue comment whose parent
   number appears in `prs_updated` is counted under
   `pr_conversation_comments`; an unknown parent number falls into
   `issue_comments`. `review_comments.json` entries all counted under
   `review_comments`. Assert both `team` and `total` breakdowns.
5. **Zero-denominator layer.** No comments in window →
   `comments.team == {...all zeros...}`, `total == {...all zeros...}`,
   `share is None`. No `ZeroDivisionError`.
6. **New shape presence.** `team_share` keys are exactly
   `["commits", "pr", "comments"]`. Old flat keys are gone.

### `tests/test_report.py` — new / updated

1. **`_aggregate` sums sub-metrics per layer** across two repos: each
   `team.<sub>` and `total.<sub>` sums; aggregate `share` re-derived from
   the summed sub-totals (not the mean of per-repo shares).
2. **`_aggregate` skips errored repos.** Two-repo fixture, one errored →
   aggregate `team_share` reflects only the healthy one.
3. **`_aggregate` unions truncation.** Existing rule; keep the existing
   assertion, adjust for the new shape only if it references `team_share`.
4. **`_chart_data.team_share` shape.** `layers == ["commits", "pr", "comments"]`;
   `team`, `total`, `shares` are length-3 arrays aligned to `layers`;
   `breakdown` has one entry per layer, each with `team` and `total`
   sub-dicts using raw sub-metric names.
5. **`_chart_data.team_share` invariants.** For every layer,
   `sum(breakdown[layer].team.values()) == team[i]`, same for `total`,
   and `shares[i] == team[i] / total[i]` (or `None`).
6. **`team_share` layer disabled.** `_chart_data` omits `team_share`;
   `render(metrics)` produces HTML with the "layer disabled" placeholder
   in the top row and no `data-chart="team_share"` canvas.
7. **Zero-total layer.** `shares[i] is None`; rendered HTML still valid;
   no `data-chart="team_share"` canvas rendered for that layer (placeholder
   instead — asserted via placeholder text or class).

### Fixtures

- Add `prs_by_merged.json` to `tests/fixtures/team_share/raw/acme__api/`
  so the PR-layer fixture reflects the full input.
- Update any existing team_share assertions in `tests/test_metrics.py` and
  `tests/test_report.py` that reference the old flat keys.

### Manual verification

- Run `python3 -m gh_contributions.report` against a fresh `out/` directory
  and open `report.html` in a browser. Confirm: three doughnuts in the top
  row line up left-to-right (Commits blue, PR green, Comments yellow);
  center title shows layer name + share percent; tooltip on both slices
  includes the sub-metric parenthetical for the PR and Comments pies and
  omits it for the Commits pie; layer-disabled placeholder appears when
  `team_share` is dropped from config.

### JS testing

None added — no JS test infrastructure exists today. Manual verification
per above.

## Non-goals / explicitly not doing

- No feature flag or config option to fall back to the old bar-chart
  team-share layout. The old cell, chart-data key, and JS branch are
  deleted.
- No change to `authoring` / `collaboration` metric layers, the per-user
  activity chart, or the details table.
- No change to the extraction layer (`gh_contributions/fetch.py`).
  `prs_by_merged.json` is already fetched unconditionally.
- No change to the metric catalog spec
  (`docs/superpowers/specs/2026-07-01-team-activity-metrics-design.md`);
  layer definitions here are a presentation choice, not a metric
  redefinition.
