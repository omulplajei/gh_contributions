# Unified per-user activity chart — design

Status: draft
Date: 2026-07-02

## Goal

Consolidate the three per-user charts (Authoring, Reviews given, Comments) in
each repo tab of the HTML report into a single horizontal stacked bar chart
that shows every developer's total contributions broken into three intuitive
layers: commits, PR activity, and comments. The Team share chart and the
per-user details table are unchanged.

## Scope

In scope:

- New per-repo `activity` payload block emitted by `_chart_data` in
  `gh_contributions/report.py`.
- New horizontal stacked bar chart rendered in the browser from that payload.
- Layout change in the repo tab body (Team share on top, activity chart
  full-width below, details table unchanged at the bottom).
- Removal of the `authoring`, `reviews`, and `comments` chart cells and their
  corresponding JS render branches.
- Unit tests for the new aggregation.

Out of scope:

- Any change to `metrics.json` produced by `gh_contributions/metrics.py`.
- Any change to the `_aggregate` cross-repo sum (it keeps producing the same
  shape; `_chart_data` runs on top of it unchanged).
- Any change to the per-user details table below the chart.
- Chart.js version bump; asset changes.
- JS unit tests (no JS test infrastructure exists today).

## Layer definitions

The chart has exactly three stack layers per user. The mapping from raw
per-user metrics is fixed:

| Layer      | Sub-metrics summed                                                                                                                                                                                     |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `commits`  | `authoring.commits`                                                                                                                                                                                    |
| `pr`       | `authoring.pull_requests_opened` + `authoring.pull_requests_merged` + `collaboration.reviews_given.APPROVED` + `collaboration.reviews_given.CHANGES_REQUESTED` + `collaboration.reviews_given.COMMENTED` |
| `comments` | `collaboration.review_comments` + `collaboration.pr_conversation_comments` + `collaboration.issue_comments`                                                                                             |

Metrics that stay in the raw per-user payload but are excluded from every
stack layer:

- `authoring.issues_opened`
- `collaboration.cross_team_reviews` (already a subset of reviews_given,
  informational signal only)

Both remain visible in the details table.

## Sort order

Users are pre-sorted by total contributions (sum of all three layers)
descending, with alphabetical tie-break on login.

## Behavior when a config metrics layer is disabled

The chart always renders all three stack layers. When the config's
`metrics:` list omits `authoring` or `collaboration`, the affected sub-metrics
simply contribute 0 to their layer. A small text note above the chart names
any disabled layer(s) and their consequence, for example:

> Note: `collaboration` metrics layer disabled in config — PR reviews and
> comments count as 0.

The note is derived client-side from `payload.run.metrics_layers`; no new
field is added to the payload for this purpose.

## Data shape

`_chart_data` in `gh_contributions/report.py` gains a new `activity` key in
its return dict, and drops the existing `authoring`, `reviews`, and
`comments` keys. The `team_share` key and `per_user_raw` key are unchanged.

```json
{
  "error": null,
  "per_user_raw": { ... },
  "team_share":   { ... },
  "activity": {
    "users":  ["alice", "bob", "carol"],
    "totals": [43, 29, 12],
    "layers": {
      "commits":  [12,  8, 3],
      "pr":       [24, 10, 7],
      "comments": [ 7, 11, 2]
    },
    "breakdown": {
      "alice": {
        "commits":  { "commits": 12 },
        "pr": {
          "pull_requests_opened":  3,
          "pull_requests_merged":  3,
          "APPROVED":              4,
          "CHANGES_REQUESTED":     1,
          "COMMENTED":            13
        },
        "comments": {
          "review_comments":          2,
          "pr_conversation_comments": 3,
          "issue_comments":           2
        }
      }
    }
  }
}
```

Guarantees:

- `users` is pre-sorted (total desc, login asc).
- For each index `i`, `totals[i] == layers.commits[i] + layers.pr[i] + layers.comments[i]`.
- `breakdown[login]` contains one entry per layer (`commits`, `pr`,
  `comments`). Sub-keys use the raw metric names as they appear in the
  per-user structure — no renaming happens in JSON. Display renaming happens
  in the JS layer.
- When a repo has no users in the team, `activity` is:
  ```json
  { "users": [], "totals": [], "layers": {"commits": [], "pr": [], "comments": []}, "breakdown": {} }
  ```
- When a repo has `error != null`, `activity` is omitted (same pattern as
  the existing chart-data keys).
- The `_aggregate` cross-repo sum feeds `_chart_data` unchanged, so the
  aggregate tab produces the same `activity` shape as any single-repo tab
  without any special-case code.

## Rendering

### Layout

The repo tab body's grid is replaced with a vertical flex stack:

```
+-------------------------------------------------------------+
|  Team share  (compact, max-width ~480px, left-aligned)      |
+-------------------------------------------------------------+
|  Activity   (full-width, horizontal stacked bars,           |
|              height grows with user count)                  |
+-------------------------------------------------------------+
|  Details table  (unchanged)                                 |
+-------------------------------------------------------------+
```

CSS changes:

- `.grid` becomes `display: flex; flex-direction: column; gap: 16px;`.
- The Team share cell gets an inline `max-width: 480px`.
- `.cell canvas { max-height: 320px; }` is removed so the activity canvas
  can grow tall.

### `_tab_body` cells

The four `_cell(...)` calls in `_tab_body` collapse to two:

1. Team share (`chart_key='team_share'`, `required_layer='team_share'`) —
   unchanged.
2. Activity (`chart_key='activity'`, `required_layer=None` — always
   rendered, since layer-disabled behavior is handled inside the chart).

`_cell` is extended to accept `required_layer=None` meaning "always render".
The `authoring`, `reviews`, and `comments` cell calls are deleted.

### JS chart config

The `_APP_JS` string in `report.py` loses its three
`kind === 'authoring' | 'reviews' | 'comments'` branches and gains a single
`kind === 'activity'` branch:

- `type: 'bar'` with `indexAxis: 'y'` (horizontal orientation).
- Three datasets keyed `commits`, `pr`, `comments` with labels `"Commits"`,
  `"PR activity"`, `"Comments"`. Colors come from the existing `palette`
  (indices 0, 1, 2).
- `scales: { x: { stacked: true, beginAtZero: true }, y: { stacked: true } }`.
- `maintainAspectRatio: false`. Canvas height set inline before chart
  construction to `users.length * 28 + 60` px (min 200 px).
- Tooltip:
  - `title` callback: user login.
  - `label` callback per dataset: `"{layer name}: {layer total} ({sub1 X, sub2 Y, ...})"`.
    Sub-key names are mapped for display via a small object in JS
    (`pull_requests_opened → "opened"`, `pull_requests_merged → "merged"`,
    `APPROVED → "approved"`, `CHANGES_REQUESTED → "changes"`,
    `COMMENTED → "commented"`, `review_comments → "review"`,
    `pr_conversation_comments → "PR conv"`,
    `issue_comments → "issue"`).
    The parenthetical breakdown is omitted when (a) the layer's total is
    0, or (b) the layer has only one sub-metric (the Commits layer), since
    the parenthetical would just repeat the layer total.

### Disabled-layer note

Above the activity canvas, when `payload.run.metrics_layers` is missing
`authoring` and/or `collaboration`, a `<p class="layer-note">` is inserted
with a one-line message naming the missing layer(s). Injected client-side to
avoid duplicating the layer list in the HTML.

CSS: `.layer-note { color: #666; font-size: 12px; margin: 0 0 8px; }`.

## Testing

New unit tests in `tests/test_report.py` targeting the `activity` block
produced by `_chart_data`:

1. **Layer sums.** One user with known counts across every sub-metric;
   assert `activity.layers.commits[0]`, `pr[0]`, `comments[0]` match the
   layer definitions above.
2. **Sort order.** Three users where sums produce a non-trivial order
   including one tie between two users with the same total; assert users
   appear as `[highest, tie_a_alpha, tie_b_alpha, lowest]`.
3. **`totals[i]` invariant.** For every `i`, `totals[i]` equals the sum of
   the three layer values at that index.
4. **Breakdown structure.** `breakdown[login]['pr']` contains exactly the
   five expected sub-keys with correct values; no unrelated sub-keys leak
   into any layer's breakdown.
5. **Excluded metrics.** `issues_opened` and `cross_team_reviews` do not
   appear anywhere inside `activity`, but do appear inside `per_user_raw`
   (the details table stays intact).
6. **Layer-disabled config.** Metrics built with
   `metrics_layers=["authoring"]` and a repo whose per-user has no
   `collaboration` block: `activity.layers.comments` is all zeros, `pr`
   equals only the two authoring sub-metrics, and no `KeyError` is raised.
7. **Empty repo.** `per_user = {}` produces
   `activity.users == []`, `totals == []`, each layer array `[]`, and
   `breakdown == {}`. `render(metrics)` returns valid HTML.
8. **Aggregate tab.** Running `_aggregate` on multiple repos and passing
   its result through `_chart_data` produces the same `activity` shape as a
   single-repo call.

Existing tests to update: any assertion referencing the removed `authoring`,
`reviews`, or `comments` payload keys, or removed HTML `data-chart` cells
with those names. Assertions about the details table, Team share layout,
and `_aggregate` do not change.

No JS unit tests are added (no JS test infrastructure exists). Manual
verification: run `python3 -m gh_contributions.report` against an existing
`out/` directory and open the generated `report.html` in a browser to
inspect layout, tooltip content, and the layer-disabled note.

## Non-goals / explicitly not doing

- No feature flag or config option to fall back to the old three-chart
  layout. The old cells and JS branches are deleted.
- No normalization or weighting of the raw counts across layers (a merged
  PR and a single comment each contribute 1 to their layer's total). This
  matches the tool's existing raw-count philosophy.
- No change to the metric catalog spec
  (`docs/superpowers/specs/2026-07-01-team-activity-metrics-design.md`);
  layer definitions here are a presentation choice, not a metric
  redefinition.
