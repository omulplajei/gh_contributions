# Team share monthly trend — design

Status: draft
Date: 2026-07-02

## Goal

Add a row of three line charts to each repo tab (and the aggregate tab) of
the HTML report, one per team-share layer (Commits / PR activity / Comments).
Each line chart plots the team's monthly share % across the configured
window, with the window's aggregate share drawn as a horizontal reference
line so the reader can see at a glance whether a given month was above or
below the window average.

The three trend charts sit directly under the three existing donuts and use
the same layer colors, so each donut+trend pair reads top-to-bottom as
"whole-window aggregate → how it moved month by month".

## Scope

In scope:

- `gh_contributions/metrics.py`: refactor `_apply_team_share` to loop per
  month, and emit a `by_month` sub-key on each of the three
  `team_share.<layer>` blocks. The existing whole-window `team` / `total` /
  `share` values remain and are equal to the sums of the per-month buckets.
- `gh_contributions/report.py`:
  - `_aggregate`: sum `team_share.<layer>.by_month` across healthy repos
    per month (parallel to how the whole-window aggregate is summed today).
  - `_chart_data`: emit a new `team_share_trend` payload block alongside
    the existing `team_share`.
  - `_tab_body`: render a new `.trend-row` (three cells with `<canvas>`
    elements) sibling to the existing `.team-share-row`.
  - `_APP_JS`: new `renderTrend()` function; wire it up in the same
    per-tab render pass that already draws the donuts.
  - CSS: three-column grid for `.trend-row`, aligned to the donut columns
    above; fixed chart height (~160 px).
- Unit tests for the new metric shape, `_aggregate` behavior, and
  `_chart_data` output.
- Fixture: add a second month bucket to `tests/fixtures/team_share/` so a
  real multi-month trend can be asserted end-to-end.

Out of scope:

- Any change to `authoring` / `collaboration` per-user layers or their
  aggregation.
- Per-user trends (only team-vs-total share is trended; individual devs
  stay in the aggregate stacked bar chart).
- Trends beyond the configured window (no cross-run history).
- Chart type toggles, alternate y-axis modes, downloadable data.
- Chart.js version bump; asset changes.
- JS unit tests (no JS test infrastructure exists today).
- New config keys or CLI flags.

## Layer definitions

The three trended layers are the same as the existing donut layers, using
the same sub-metric sums. No new layer definitions are introduced.

| Layer      | Sub-metrics summed                                                                                                    |
| ---------- | --------------------------------------------------------------------------------------------------------------------- |
| `commits`  | `commits`                                                                                                             |
| `pr`       | `pull_requests_opened` + `pull_requests_merged` + `APPROVED` + `CHANGES_REQUESTED` + `COMMENTED`                      |
| `comments` | `review_comments` + `pr_conversation_comments` + `issue_comments`                                                     |

## Metric shape (metrics.json)

Each `team_share.<layer>` block gains a `by_month` sub-key. The existing
`team`, `total`, and `share` keys are unchanged; they equal the sums of
the corresponding `by_month` buckets.

```json
"team_share": {
  "commits": {
    "team":  {"commits": 56},
    "total": {"commits": 262},
    "share": 0.2137,
    "by_month": {
      "2026-01": {"team": {"commits": 20}, "total": {"commits": 65}, "share": 0.3077},
      "2026-02": {"team": {"commits":  8}, "total": {"commits": 20}, "share": 0.4000},
      "2026-03": {"team": {"commits":  2}, "total": {"commits":  8}, "share": 0.2500}
    }
  },
  "pr":       { ... same shape, PR sub-metric keys as today ... },
  "comments": { ... same shape, comment sub-metric keys as today ... }
}
```

Rules for `by_month`:

- Month keys use the same `YYYY-MM` format as the raw cache buckets.
- **Every month in the configured window is present**, including:
  - Months where the layer's total activity is 0 → `team` and `total` sub-metric
    counts are all `0`, `share` is `null`.
  - Months whose bucket failed or is absent → same treatment as zero-total:
    all counts `0`, `share` `null`. This keeps the month list stable across
    layers and across repos so the report can render aligned x-axes without
    per-layer branching.
- `share` is `null` iff the sum of `total.values()` is `0`; otherwise it is
  a float in `[0, 1]`.

## Report payload shape (chart data)

`_chart_data` in `report.py` gains a new `team_share_trend` key on the repo
payload. Same shape is produced by `_aggregate` for the aggregate tab.

```json
"team_share_trend": {
  "months": ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"],
  "commits": {
    "share":           [0.31, 0.40, 0.25, null, 0.22, 0.18],
    "team":            [20,   8,   2,    0,    4,    3],
    "total":           [65,   20,  8,    0,    18,   17],
    "aggregate_share": 0.2137
  },
  "pr":       { ... same shape ... },
  "comments": { ... same shape ... }
}
```

- `months` is emitted once and shared across all three layers so the x-axes
  line up.
- Each layer has three parallel arrays of equal length:
  - `share` (nullable float `[0, 1]`) — the plotted value. Nulls create
    gaps in the line.
  - `team` (non-negative int) — sum of team sub-metric counts for that
    month; used in the tooltip.
  - `total` (non-negative int) — sum of total sub-metric counts for that
    month; used in the tooltip.
- `aggregate_share` (nullable float `[0, 1]`) — the value drawn as the
  horizontal reference line, equal to the donut's `share` for the same
  layer. `null` iff the whole-window `total` is `0`, in which case no
  reference line is drawn.

## Layout & visuals

Per-tab layout (both per-repo tabs and the aggregate tab):

```
[ Donut: Commits ]     [ Donut: PR activity ]   [ Donut: Comments ]
[ Trend: Commits ]     [ Trend: PR activity ]   [ Trend: Comments ]
[            Per-user stacked bar chart (unchanged)           ]
[                    Details table (unchanged)                 ]
```

Each trend chart occupies the same column width as its donut; the trend
row uses the same three-column grid as `.team-share-row`.

Per trend chart:

- **Type:** Chart.js `line`.
- **X-axis:** category axis; labels are the short month name derived from
  `months[i]` (e.g. `2026-01` → `Jan`). Year is not repeated because a
  single report covers one contiguous window.
- **Y-axis:** fixed `0`–`100 %`, `stepSize: 25`, tick label `${v}%`.
  Fixed range keeps the three trend charts visually comparable and gives
  the reference line consistent meaning.
- **Line color:** matches the donut's team slice color for that layer
  (blue = commits, green = PR activity, orange = comments). Reuse the
  existing color constants in the JS.
- **Data points:** small filled dots (`pointRadius: 3`). Nulls in `share`
  produce gaps in the line (`spanGaps: false`).
- **Reference line:** second dataset, constant value `aggregate_share *
  100` across all x-values, `borderDash: [4, 4]`, gray, `pointRadius: 0`,
  excluded from the legend and the tooltip. Omitted entirely when
  `aggregate_share` is `null`.
- **Tooltip on hover (single point):**
  `<Month YYYY> — <share.percent>% (team <team[i]> of total <total[i]>)`.
  Formatted client-side from the parallel arrays.
- **Chart height:** ~160 px, matching the compact donut row above.
- **Caption:** small text below the chart, `Monthly share`. No chart title
  (the donut above already names the layer).
- **Empty state:** if every value in `share` is `null`, the canvas is
  replaced with `<p class="empty">No data for this window.</p>`. The
  layout cell is preserved so the row alignment does not collapse.

## Aggregation across repos (aggregate tab)

`_aggregate` in `report.py` extends its existing per-layer sum to also sum
`by_month` buckets:

```
agg.team_share.<layer>.by_month[m].team  = sum over healthy repos of
                                            r.team_share.<layer>.by_month[m].team
agg.team_share.<layer>.by_month[m].total = sum over healthy repos of
                                            r.team_share.<layer>.by_month[m].total
agg.team_share.<layer>.by_month[m].share = team_sum / total_sum
                                            (null if total_sum == 0)
```

The month list on the aggregate is the union of month keys seen across
healthy repos, sorted ascending. In practice all healthy repos share the
same month list because they come from the same configured window and use
the same `_months_between` enumeration.

## Error handling and edge cases

- **Single-month window:** the line degenerates to a single dot. The
  reference line still renders (a horizontal dashed line at the donut's
  aggregate value, which will coincide with the single dot). Acceptable.
- **All months null for a layer:** empty state ("No data for this window.")
  replaces just that chart's canvas; the other two trend charts render
  normally.
- **Repo-level error:** repos with a top-level `error` are excluded from
  `_aggregate` as they are today; they simply don't have a rendered tab
  body (existing behavior).
- **Partial per-month failures:** already surfaced by the existing
  per-repo error banner. Failed months contribute `share: null` to the
  trend and no dot is drawn — the reader sees the gap.
- **Currently-excluded current month:** unchanged. The window continues to
  end at the last day of the previous calendar month
  (`_effective_end`), so the trend never plots a partial month.

## Testing

`tests/test_metrics.py`:

- New multi-month fixture (extended `tests/fixtures/team_share/`) exercises:
  - `team_share.<layer>.by_month` has an entry for every configured month.
  - A month with zero total activity has `share: null` and all counts `0`.
  - A month whose bucket is absent has the same shape as a zero-total
    month (`share: null`, counts `0`).
  - Invariant: whole-window `team` / `total` equals the sum of `by_month`
    `team` / `total` for the same layer.

`tests/test_report.py`:

- `_chart_data` returns `team_share_trend` with parallel arrays of equal
  length; each layer's array length equals `len(months)`.
- Nulls in `share` propagate correctly (input `share: null` → output
  `null` in the array at the same index).
- `_aggregate` on two repos correctly sums `by_month` per layer and
  produces a `null` share for a month where both repos' totals are 0.
- `aggregate_share` on the payload equals the corresponding donut's
  `share`.

No JS unit tests (no infra today). Visual verification via a manual run
against `out/2026-07-02T123016Z/` (the current 6-month cache) is expected
as part of the plan's verification step.
