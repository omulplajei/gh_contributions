# Team share monthly trend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a row of three line charts under the existing donut row (one per team-share layer) that plots the team's monthly share % across the configured window, with the window's aggregate share drawn as a horizontal reference line.

**Architecture:** `metrics.py` refactors `_apply_team_share` to loop per month and emit a new `by_month` sub-key on each of the three `team_share.<layer>` blocks; the whole-window `team`/`total`/`share` values are then derived as sums. `report.py` sums those per-month buckets across healthy repos in `_aggregate`, transforms them into Chart.js-friendly parallel arrays in `_chart_data` (`team_share_trend`), and renders a new `.trend-row` beneath the existing `.team-share-row`. Inline JS gets a `team_share_trend` render branch (line chart + dashed reference line, month-name x-labels, `0`–`100 %` y-axis).

**Tech Stack:** Python 3 (stdlib only), pytest, Chart.js UMD (vendored). No new dependencies.

## Global Constraints

- No new runtime dependencies. Chart.js remains at the vendored version in `gh_contributions/assets/chart.umd.min.js`.
- Pure Python is TDD'd; JS/CSS is smoke-tested manually. No JS test infra is added.
- `metrics.json` gains a new `by_month` sub-key on each `team_share.<layer>` block; the existing `team`/`total`/`share` keys stay and must equal the sums of the `by_month` buckets (invariant).
- Layer keys are exactly `commits`, `pr`, `comments`. Sub-metric keys inside `team`/`total` are unchanged from the current shape.
- `by_month` includes **every** month in the configured window enumerated by `_months_between(cfg.since, today)`. Months whose bucket is absent OR errored produce a zero-count entry with `share: null`, identical to a month whose sub-metric totals happened to be 0. This keeps month lists aligned across layers, repos, and the aggregate tab.
- Palette color assignment (unchanged): `palette[0]` (blue) for `commits`, `palette[1]` (green) for `pr`, `palette[2]` (yellow) for `comments`. Trend line color matches its layer's donut team-slice color.
- The current-month exclusion (`_effective_end`) stays as-is; the trend never plots a partial month.

---

## Task 1: `metrics.py` — refactor `_apply_team_share` to compute per month and emit `by_month`

Rewrites the whole-window aggregation as a per-month loop. Every month enumerated by `_months_between` produces one `by_month` entry (good months carry real counts; errored/absent months carry zeros with `share: null`). Whole-window `team`/`total` become sums; `share` becomes the ratio of those sums (`null` if total is 0). Existing tests keep passing because the whole-window shape doesn't change.

**Files:**
- Modify: `gh_contributions/metrics.py`
- Modify: `tests/test_metrics.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `team_share.<layer>.by_month: dict[str, dict]` on every repo entry. Each `by_month[m]` has the shape `{"team": {sub: int, ...}, "total": {sub: int, ...}, "share": float | None}`. Sub-metric keys are the same as the existing `team`/`total` for that layer. Whole-window `team`/`total`/`share` are unchanged in shape and equal to the sums of `by_month`.

- [ ] **Step 1: Read the current `_apply_team_share` to remember the exact sub-metric key order per layer**

Open `gh_contributions/metrics.py` and locate `_apply_team_share`. Note the sub-metric key sets used today for each layer:

- `commits`  → `("commits",)`
- `pr`       → `("pull_requests_opened", "pull_requests_merged", "APPROVED", "CHANGES_REQUESTED", "COMMENTED")`
- `comments` → `("review_comments", "pr_conversation_comments", "issue_comments")`

Later steps must preserve these key orders exactly (existing tests assert them).

- [ ] **Step 2: Write failing test — `by_month` present for every configured month across a two-month window**

Add to `tests/test_metrics.py`:

```python
def test_team_share_by_month_present_for_every_month(tmp_path) -> None:
    from gh_contributions.config import Config
    from datetime import date
    import json as _json

    cfg = Config(
        usernames=["alice"],
        repos=["acme/api"],
        since=date(2026, 2, 1),
        metrics=["team_share"],
    )
    # Month 2026-02 has real activity; 2026-03 has an empty bucket.
    for m in ("2026-02", "2026-03"):
        bucket = tmp_path / m / "acme__api"
        bucket.mkdir(parents=True)
        (bucket / "_meta.json").write_text("{}")
        for f in ("commits.json", "prs_by_created.json", "prs_by_merged.json",
                  "prs_updated.json", "review_comments.json", "issue_comments.json"):
            (bucket / f).write_text("[]")
        (bucket / "reviews").mkdir()

    (tmp_path / "2026-02" / "acme__api" / "commits.json").write_text(_json.dumps([
        {"author": {"login": "alice"}, "committer": {"date": "2026-02-05T10:00:00Z"}},
        {"author": {"login": "eve"},   "committer": {"date": "2026-02-06T10:00:00Z"}},
    ]))

    out = compute(tmp_path, cfg, today=date(2026, 4, 15))
    share = out["repos"]["acme/api"]["team_share"]

    assert set(share["commits"]["by_month"]) == {"2026-02", "2026-03"}

    feb = share["commits"]["by_month"]["2026-02"]
    assert feb == {"team": {"commits": 1}, "total": {"commits": 2}, "share": 0.5}

    mar = share["commits"]["by_month"]["2026-03"]
    assert mar == {"team": {"commits": 0}, "total": {"commits": 0}, "share": None}
```

- [ ] **Step 3: Run the new test to verify it fails**

Run: `python3 -m pytest tests/test_metrics.py::test_team_share_by_month_present_for_every_month -v`
Expected: FAIL with `KeyError: 'by_month'` (or similar — the key doesn't exist yet).

- [ ] **Step 4: Refactor `_apply_team_share` in `gh_contributions/metrics.py`**

Replace the current `_apply_team_share` body with a per-month loop. The full replacement:

```python
def _apply_team_share(
    raw_root: Path,
    months: list[str],
    owner: str,
    name: str,
    config: Config,
    today: date,
    out: dict,
) -> None:
    layers_sub_keys = {
        "commits":  ("commits",),
        "pr":       ("pull_requests_opened", "pull_requests_merged",
                     "APPROVED", "CHANGES_REQUESTED", "COMMENTED"),
        "comments": ("review_comments", "pr_conversation_comments", "issue_comments"),
    }

    def _empty_layer(sub_keys: tuple[str, ...]) -> dict:
        return {
            "team":  {k: 0 for k in sub_keys},
            "total": {k: 0 for k in sub_keys},
            "share": None,
        }

    by_month: dict[str, dict[str, dict]] = {}
    good_set = set(months)  # `months` here is the good_months list from _compute_repo
    all_months = _months_between(config.since, today)

    for m in all_months:
        if m in good_set:
            by_month[m] = _compute_month_team_share(
                raw_root, m, owner, name, config, today, layers_sub_keys,
            )
        else:
            by_month[m] = {layer: _empty_layer(subs)
                           for layer, subs in layers_sub_keys.items()}

    # Whole-window aggregate = sum of per-month buckets.
    aggregate: dict[str, dict] = {}
    for layer, subs in layers_sub_keys.items():
        team  = {k: sum(by_month[m][layer]["team"][k]  for m in all_months) for k in subs}
        total = {k: sum(by_month[m][layer]["total"][k] for m in all_months) for k in subs}
        t = sum(team.values())
        n = sum(total.values())
        aggregate[layer] = {
            "team":     team,
            "total":    total,
            "share":    (t / n) if n else None,
            "by_month": {m: by_month[m][layer] for m in all_months},
        }

    out["team_share"] = aggregate
```

And add a new module-private helper `_compute_month_team_share` (place it directly above `_apply_team_share`). It's the current single-window logic scoped to one month:

```python
def _compute_month_team_share(
    raw_root: Path,
    month: str,
    owner: str,
    name: str,
    config: Config,
    today: date,
    layers_sub_keys: dict[str, tuple[str, ...]],
) -> dict:
    team = set(config.usernames)
    # For per-month share, the review-comment / issue-comment / review windowing
    # is bounded to the month itself, not the whole window.
    m_start_str = f"{month}-01"
    from calendar import monthrange
    y_s, mo_s = month.split("-", 1)
    y, mo = int(y_s), int(mo_s)
    last_day = monthrange(y, mo)[1]
    lo = datetime.fromisoformat(f"{m_start_str}T00:00:00+00:00")
    hi = datetime.fromisoformat(f"{month}-{last_day:02d}T23:59:59+00:00")

    def _in_month(ts: str | None) -> bool:
        d = _parse_ts(ts)
        return d is not None and lo <= d <= hi

    commits_team = 0
    commits_total = 0
    for c in _load_endpoint(raw_root, [month], owner, name, "commits.json"):
        commits_total += 1
        if _author_login(c, "commits.json") in team:
            commits_team += 1

    opened_team, opened_total = 0, 0
    for p in _load_endpoint(raw_root, [month], owner, name, "prs_by_created.json"):
        opened_total += 1
        if _author_login(p, "prs_by_created.json") in team:
            opened_team += 1

    merged_team, merged_total = 0, 0
    for p in _load_endpoint(raw_root, [month], owner, name, "prs_by_merged.json"):
        merged_total += 1
        if _author_login(p, "prs_by_merged.json") in team:
            merged_team += 1

    rev_team = {s: 0 for s in _REVIEW_STATES}
    rev_total = {s: 0 for s in _REVIEW_STATES}
    for reviews in _load_reviews(raw_root, [month], owner, name).values():
        for r in reviews:
            state = r.get("state")
            if state not in _REVIEW_STATES:
                continue
            if not _in_month(r.get("submitted_at")):
                continue
            rev_total[state] += 1
            if ((r.get("user") or {}).get("login")) in team:
                rev_team[state] += 1

    rc_team, rc_total = 0, 0
    for c in _load_endpoint(raw_root, [month], owner, name, "review_comments.json"):
        if not _in_month(c.get("created_at")):
            continue
        rc_total += 1
        if ((c.get("user") or {}).get("login")) in team:
            rc_team += 1

    prs_updated = _load_endpoint(raw_root, [month], owner, name, "prs_updated.json")
    known_pr_numbers = {
        p.get("number") for p in prs_updated if isinstance(p.get("number"), int)
    }
    prc_team, prc_total = 0, 0
    ic_team, ic_total = 0, 0
    for c in _load_endpoint(raw_root, [month], owner, name, "issue_comments.json"):
        if not _in_month(c.get("created_at")):
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

    return {
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

Notes:
- The old `_window_bounds`, `_in_window`, and existing per-month sums that used `lo/hi = full window` are replaced by per-month `_in_month`. The whole-window `share` value stays numerically identical because it's now `sum(team_per_month) / sum(total_per_month)`, which equals the previous single-window computation.
- Move the local `from calendar import monthrange` import to the top of the file if it isn't there already; `_month_bounds` in `fetch.py` uses it, but `metrics.py` may not.

- [ ] **Step 5: Run the new test to verify it passes**

Run: `python3 -m pytest tests/test_metrics.py::test_team_share_by_month_present_for_every_month -v`
Expected: PASS.

- [ ] **Step 6: Update pre-existing dict-equality assertions to include `by_month`**

Two existing tests in `tests/test_metrics.py` do full dict-equality on a `team_share.<layer>` block; after Task 1 those dicts contain a new `by_month` key and the assertions will fail. Update them to include `by_month`.

In `test_team_share_happy_path`, replace:

```python
assert share["commits"] == {
    "team":  {"commits": 4},
    "total": {"commits": 10},
    "share": pytest.approx(4 / 10),
}
```

with:

```python
assert share["commits"]["team"]  == {"commits": 4}
assert share["commits"]["total"] == {"commits": 10}
assert share["commits"]["share"] == pytest.approx(4 / 10)
assert "by_month" in share["commits"]
```

In `test_team_share_zero_denominator_is_null`, replace:

```python
assert share["commits"] == {
    "team":  {"commits": 0},
    "total": {"commits": 0},
    "share": None,
}
```

with:

```python
assert share["commits"]["team"]  == {"commits": 0}
assert share["commits"]["total"] == {"commits": 0}
assert share["commits"]["share"] is None
assert "by_month" in share["commits"]
```

- [ ] **Step 7: Run the full metrics test file to verify existing tests still pass**

Run: `python3 -m pytest tests/test_metrics.py -v`
Expected: all tests pass.

- [ ] **Step 8: Add invariant test — sum of `by_month` equals whole-window totals**

Append to `tests/test_metrics.py`:

```python
def test_team_share_by_month_sum_equals_whole_window() -> None:
    out = _load("team_share")
    share = out["repos"]["acme/api"]["team_share"]
    for layer in ("commits", "pr", "comments"):
        by_month = share[layer]["by_month"]
        for k in share[layer]["team"]:
            assert share[layer]["team"][k] == sum(m["team"][k] for m in by_month.values())
        for k in share[layer]["total"]:
            assert share[layer]["total"][k] == sum(m["total"][k] for m in by_month.values())
```

- [ ] **Step 9: Run the invariant test to verify it passes**

Run: `python3 -m pytest tests/test_metrics.py::test_team_share_by_month_sum_equals_whole_window -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add gh_contributions/metrics.py tests/test_metrics.py
git commit -m "feat(metrics): add team_share.by_month per-layer breakdown"
```

---

## Task 2: `report.py` — extend `_aggregate` to sum `by_month` across repos

Extends the cross-repo aggregation so the "All repos" tab has `team_share.<layer>.by_month` computed as the per-month sum of healthy repos.

**Files:**
- Modify: `gh_contributions/report.py`
- Modify: `tests/test_report.py`

**Interfaces:**
- Consumes: from Task 1, each per-repo `team_share.<layer>` block has a `by_month: dict[str, {"team": {...}, "total": {...}, "share": float | None}]` sub-key.
- Produces: on the aggregate repo (`__aggregate__`), the same `by_month` shape, computed as the sum of team/total sub-metric counts across healthy repos per month. `share` on each aggregate month is `team_sum / total_sum` (or `None` if total sums to 0).

- [ ] **Step 1: Extend `_ts` helper in `tests/test_report.py` to accept a `by_month` arg (default: empty dict)**

Locate `_ts` in `tests/test_report.py`. Replace the `layer(pair, sub_keys)` inner helper and the return dict so the helper accepts optional per-layer `by_month` maps. Full new `_ts`:

```python
def _ts(commits=(0, 0), pr=(0, 0), comments=(0, 0), by_month=None):
    """Build a new-shape team_share block. Puts the (team, total) totals in
    the first sub-key of each layer; other sub-keys are 0.
    `by_month` is a dict keyed by layer name, each value being the by_month
    dict to attach to that layer. Missing layers get an empty by_month."""
    by_month = by_month or {}
    def layer(pair, sub_keys, layer_name):
        team_t, total_t = pair
        team_map  = {k: 0 for k in sub_keys}
        total_map = {k: 0 for k in sub_keys}
        team_map[sub_keys[0]]  = team_t
        total_map[sub_keys[0]] = total_t
        return {
            "team":     team_map,
            "total":    total_map,
            "share":    (team_t / total_t) if total_t else None,
            "by_month": by_month.get(layer_name, {}),
        }
    return {
        "commits":  layer(commits,  _TEAM_SHARE_SUB_METRICS["commits"],  "commits"),
        "pr":       layer(pr,       _TEAM_SHARE_SUB_METRICS["pr"],       "pr"),
        "comments": layer(comments, _TEAM_SHARE_SUB_METRICS["comments"], "comments"),
    }
```

Also update `test_aggregate_sums_team_share_sub_metrics_per_layer`'s inline `repo_with_pr` helper: add a `"by_month": {}` entry to each layer in the constructed `team_share` dict so it matches the new shape. Full replacement for the `team_share` construction inside `repo_with_pr`:

```python
return {
    "per_user": {},
    "team_share": {
        "commits":  {**_ts()["commits"]},
        "pr":       {**pr, "by_month": {}},
        "comments": {**_ts()["comments"]},
    },
    "truncated": {},
    "error": None,
}
```

- [ ] **Step 2: Update pre-existing dict-equality assertions in `tests/test_report.py` to include `by_month`**

Two existing tests do full dict-equality on an aggregate `team_share.<layer>` block; after Task 2 those dicts contain a new `by_month` key. Update them.

In `test_aggregate_recomputes_team_share_ratios`, replace:

```python
assert agg["team_share"]["commits"] == {
    "team":  {"commits": 10},
    "total": {"commits": 15},
    "share": pytest.approx(10 / 15),
}
```

with:

```python
assert agg["team_share"]["commits"]["team"]  == {"commits": 10}
assert agg["team_share"]["commits"]["total"] == {"commits": 15}
assert agg["team_share"]["commits"]["share"] == pytest.approx(10 / 15)
assert "by_month" in agg["team_share"]["commits"]
```

In `test_aggregate_skips_errored_repos_but_uses_healthy_ones`, replace:

```python
assert agg["team_share"]["commits"] == {
    "team":  {"commits": 4},
    "total": {"commits": 8},
    "share": 0.5,
}
```

with:

```python
assert agg["team_share"]["commits"]["team"]  == {"commits": 4}
assert agg["team_share"]["commits"]["total"] == {"commits": 8}
assert agg["team_share"]["commits"]["share"] == 0.5
assert "by_month" in agg["team_share"]["commits"]
```

- [ ] **Step 3: Run existing report tests to verify the helper change is backward-compatible**

Run: `python3 -m pytest tests/test_report.py -v`
Expected: all tests pass (the new `by_month` key is populated on every layer, defaulting to empty; the updated equality assertions now allow the extra key).

- [ ] **Step 4: Write failing test — `_aggregate` sums `by_month` across two repos**

Append to `tests/test_report.py` (in the `# ---------- _aggregate ----------` section):

```python
def test_aggregate_sums_team_share_by_month_across_repos() -> None:
    m1 = {
        "commits": {
            "2026-02": {"team": {"commits": 2}, "total": {"commits": 5}, "share": 0.4},
            "2026-03": {"team": {"commits": 0}, "total": {"commits": 0}, "share": None},
        },
    }
    m2 = {
        "commits": {
            "2026-02": {"team": {"commits": 1}, "total": {"commits": 3}, "share": 1/3},
            "2026-03": {"team": {"commits": 0}, "total": {"commits": 0}, "share": None},
        },
    }
    metrics = _metrics({
        "acme/api": _repo(ts=_ts(commits=(2, 5), by_month=m1)),
        "acme/web": _repo(ts=_ts(commits=(1, 3), by_month=m2)),
    })
    agg = _aggregate(metrics)
    bm = agg["team_share"]["commits"]["by_month"]

    assert set(bm) == {"2026-02", "2026-03"}
    assert bm["2026-02"] == {"team": {"commits": 3}, "total": {"commits": 8}, "share": pytest.approx(3 / 8)}
    assert bm["2026-03"] == {"team": {"commits": 0}, "total": {"commits": 0}, "share": None}
```

- [ ] **Step 5: Run the new test to verify it fails**

Run: `python3 -m pytest tests/test_report.py::test_aggregate_sums_team_share_by_month_across_repos -v`
Expected: FAIL (empty `by_month` on the aggregate; assertion on `bm["2026-02"]` fails).

- [ ] **Step 6: Extend `_aggregate` in `gh_contributions/report.py`**

Locate the `team_share` block inside `_aggregate` (currently builds `team`, `total`, `share` per layer). After the existing per-layer aggregate loop, insert a `by_month` computation. Full replacement for the whole `team_share` block:

```python
team_share = None
ts_repos = [v.get("team_share") for v in healthy.values() if v.get("team_share")]
if ts_repos:
    team_share = {}
    for layer, sub_keys in _TEAM_SHARE_SUB_METRICS.items():
        team  = {k: sum(ts[layer]["team"].get(k, 0)  for ts in ts_repos) for k in sub_keys}
        total = {k: sum(ts[layer]["total"].get(k, 0) for ts in ts_repos) for k in sub_keys}
        t = sum(team.values())
        n = sum(total.values())

        # Union of month keys across healthy repos; in practice all repos
        # share the same list (same _months_between), but union is safe.
        months_seen: set[str] = set()
        for ts in ts_repos:
            months_seen.update((ts[layer].get("by_month") or {}).keys())
        by_month: dict[str, dict] = {}
        for m in sorted(months_seen):
            m_team  = {k: 0 for k in sub_keys}
            m_total = {k: 0 for k in sub_keys}
            for ts in ts_repos:
                bm = (ts[layer].get("by_month") or {}).get(m)
                if not bm:
                    continue
                for k in sub_keys:
                    m_team[k]  += bm["team"].get(k, 0)
                    m_total[k] += bm["total"].get(k, 0)
            mt = sum(m_team.values())
            mn = sum(m_total.values())
            by_month[m] = {
                "team":  m_team,
                "total": m_total,
                "share": (mt / mn) if mn else None,
            }

        team_share[layer] = {
            "team":     team,
            "total":    total,
            "share":    (t / n) if n else None,
            "by_month": by_month,
        }
```

- [ ] **Step 7: Run the new test to verify it passes**

Run: `python3 -m pytest tests/test_report.py::test_aggregate_sums_team_share_by_month_across_repos -v`
Expected: PASS.

- [ ] **Step 8: Run full report tests to check nothing regressed**

Run: `python3 -m pytest tests/test_report.py -v`
Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add gh_contributions/report.py tests/test_report.py
git commit -m "feat(report): aggregate team_share.by_month across repos"
```

---

## Task 3: `report.py` — emit `team_share_trend` payload in `_chart_data`

Transforms `team_share.<layer>.by_month` into Chart.js-friendly parallel arrays and attaches them to each repo's payload under a new `team_share_trend` key. This is a pure data-shape task; no HTML/JS changes yet.

**Files:**
- Modify: `gh_contributions/report.py`
- Modify: `tests/test_report.py`

**Interfaces:**
- Consumes: from Tasks 1 and 2, `team_share.<layer>.by_month` on every repo (including `__aggregate__`).
- Produces: on each repo's chart payload, a new `team_share_trend` block:
  ```json
  {
    "months": ["2026-02", "2026-03"],
    "commits":  {"share": [0.4, null], "team": [2, 0], "total": [5, 0], "aggregate_share": 0.4},
    "pr":       {...},
    "comments": {...}
  }
  ```
  `months` is the sorted union of month keys across the three layers (in practice all three are identical). Each layer's `share`, `team`, `total` arrays have the same length as `months`, positionally aligned. `aggregate_share` equals the corresponding donut `share` (the whole-window `team_share.<layer>.share`).

- [ ] **Step 1: Write failing test — `_chart_data` emits `team_share_trend` with parallel arrays**

Add a new section in `tests/test_report.py` after the `_aggregate` tests. First locate `render`'s import in the test file:

```python
from gh_contributions.report import _aggregate, render, main
```

Change it to also import `_chart_data`:

```python
from gh_contributions.report import _aggregate, _chart_data, render, main
```

Then append this test:

```python
# ---------- _chart_data.team_share_trend ----------


def test_chart_data_emits_team_share_trend_parallel_arrays() -> None:
    by_month = {
        "commits": {
            "2026-02": {"team": {"commits": 2}, "total": {"commits": 5}, "share": 0.4},
            "2026-03": {"team": {"commits": 0}, "total": {"commits": 0}, "share": None},
        },
    }
    repo = _repo(ts=_ts(commits=(2, 5), by_month=by_month))
    cd = _chart_data(repo, {"team_share"})

    trend = cd["team_share_trend"]
    assert trend["months"] == ["2026-02", "2026-03"]

    commits = trend["commits"]
    assert commits["share"] == [pytest.approx(0.4), None]
    assert commits["team"]  == [2, 0]
    assert commits["total"] == [5, 0]
    assert commits["aggregate_share"] == pytest.approx(2 / 5)


def test_chart_data_team_share_trend_aggregate_share_null_when_total_zero() -> None:
    by_month = {
        "commits": {
            "2026-02": {"team": {"commits": 0}, "total": {"commits": 0}, "share": None},
        },
    }
    repo = _repo(ts=_ts(commits=(0, 0), by_month=by_month))
    cd = _chart_data(repo, {"team_share"})
    assert cd["team_share_trend"]["commits"]["aggregate_share"] is None
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python3 -m pytest tests/test_report.py::test_chart_data_emits_team_share_trend_parallel_arrays tests/test_report.py::test_chart_data_team_share_trend_aggregate_share_null_when_total_zero -v`
Expected: FAIL with `KeyError: 'team_share_trend'`.

- [ ] **Step 3: Extend `_chart_data` in `gh_contributions/report.py`**

Locate the `team_share` block in `_chart_data` (currently ends with `"breakdown": {...}`). Immediately after the `result["team_share"] = { ... }` assignment, add the new `team_share_trend` block:

```python
    if "team_share" in layers and repo.get("team_share"):
        ts = repo["team_share"]
        layers_list = list(_TEAM_SHARE_SUB_METRICS)

        # Union of month keys across layers, sorted ascending.
        months_seen: set[str] = set()
        for l in layers_list:
            months_seen.update((ts[l].get("by_month") or {}).keys())
        months_sorted = sorted(months_seen)

        trend: dict = {"months": months_sorted}
        for l in layers_list:
            bm = ts[l].get("by_month") or {}
            share_arr: list = []
            team_arr:  list = []
            total_arr: list = []
            for m in months_sorted:
                entry = bm.get(m)
                if entry is None:
                    share_arr.append(None)
                    team_arr.append(0)
                    total_arr.append(0)
                else:
                    share_arr.append(entry["share"])
                    team_arr.append(sum(entry["team"].values()))
                    total_arr.append(sum(entry["total"].values()))
            trend[l] = {
                "share":           share_arr,
                "team":            team_arr,
                "total":           total_arr,
                "aggregate_share": ts[l]["share"],
            }
        result["team_share_trend"] = trend
```

- [ ] **Step 4: Run the two new tests to verify they pass**

Run: `python3 -m pytest tests/test_report.py::test_chart_data_emits_team_share_trend_parallel_arrays tests/test_report.py::test_chart_data_team_share_trend_aggregate_share_null_when_total_zero -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add gh_contributions/report.py tests/test_report.py
git commit -m "feat(report): emit team_share_trend chart payload"
```

---

## Task 4: `report.py` — render `.trend-row` HTML + CSS

Adds a new `.trend-row` container beneath `.team-share-row` in each tab body, with three sibling `<canvas>` elements (one per layer) that the JS in Task 5 will bind to. Renders a server-side placeholder cell for layers whose aggregate has no data (mirrors how `_team_share_row` handles the donut placeholder).

**Files:**
- Modify: `gh_contributions/report.py`
- Modify: `tests/test_report.py`

**Interfaces:**
- Consumes: from Task 3, each repo payload has `team_share_trend.<layer>.aggregate_share` (nullable float) and `team_share_trend.months` (list of month strings) which will let the browser locate the payload.
- Produces: HTML like:
  ```html
  <div class="trend-row">
    <div class="cell cell-trend"><canvas data-chart="team_share_trend" data-repo="acme/api" data-layer="commits"></canvas></div>
    <div class="cell cell-trend"><canvas data-chart="team_share_trend" data-repo="acme/api" data-layer="pr"></canvas></div>
    <div class="cell cell-trend"><canvas data-chart="team_share_trend" data-repo="acme/api" data-layer="comments"></canvas></div>
  </div>
  ```
  When a layer has no data (`aggregate_share is None` and every value in `share` is `None`), the cell becomes `<div class="cell cell-trend trend-empty">…</div>` with text "no data in window", parallel to the pie placeholder.

- [ ] **Step 1: Write failing test — `_tab_body` HTML includes `.trend-row` with three trend canvases**

Locate `_find_payload` in `tests/test_report.py` and read the surrounding `render` tests to match the pattern. Append this test after the existing `render` tests:

```python
# ---------- render.trend-row ----------


def test_render_includes_trend_row_with_three_canvases() -> None:
    by_month = {
        "commits": {
            "2026-02": {"team": {"commits": 2}, "total": {"commits": 5}, "share": 0.4},
        },
    }
    metrics = _metrics({
        "acme/api": _repo(ts=_ts(commits=(2, 5), by_month=by_month)),
    })
    html = render(metrics)

    assert 'class="trend-row"' in html
    assert 'data-chart="team_share_trend"' in html
    assert 'data-layer="commits"' in html
    assert 'data-layer="pr"' in html
    assert 'data-layer="comments"' in html


def test_render_trend_row_shows_empty_cell_when_layer_has_no_data() -> None:
    empty_bm = {
        "commits":  {"2026-02": {"team": {"commits": 0}, "total": {"commits": 0}, "share": None}},
        "pr":       {"2026-02": {"team": {}, "total": {}, "share": None}},
        "comments": {"2026-02": {"team": {}, "total": {}, "share": None}},
    }
    metrics = _metrics({
        "acme/api": _repo(ts=_ts(by_month=empty_bm)),  # all zeros
    })
    html = render(metrics)
    # Three empty trend cells present.
    assert html.count('class="cell cell-trend trend-empty"') == 3
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python3 -m pytest tests/test_report.py::test_render_includes_trend_row_with_three_canvases tests/test_report.py::test_render_trend_row_shows_empty_cell_when_layer_has_no_data -v`
Expected: FAIL — `'class="trend-row"' in html` assertion fails.

- [ ] **Step 3: Add `_trend_row` helper in `gh_contributions/report.py`**

Insert this function directly below `_team_share_row`:

```python
def _trend_row(repo_name: str, repo: dict, layers: set) -> str:
    if "team_share" not in layers:
        return ""  # Trend row is only shown when team_share layer is enabled.
    trend = (repo.get("team_share_trend") or {})
    cells: list[str] = []
    for layer in _TEAM_SHARE_SUB_METRICS:
        layer_data = trend.get(layer) or {}
        share_arr  = layer_data.get("share") or []
        agg        = layer_data.get("aggregate_share")
        has_data = agg is not None or any(v is not None for v in share_arr)
        if not has_data:
            cells.append(
                '<div class="cell cell-trend trend-empty">'
                '<p>no data in window</p>'
                '</div>'
            )
        else:
            cells.append(
                '<div class="cell cell-trend">'
                f'<canvas data-chart="team_share_trend" data-repo="{repo_name}" data-layer="{layer}"></canvas>'
                '</div>'
            )
    return f'<div class="trend-row">{"".join(cells)}</div>'
```

- [ ] **Step 4: Wire `_trend_row` into `_tab_body`**

Locate `_tab_body` in `gh_contributions/report.py`. Modify the `parts` list so the trend row sits between the donut row and the activity cell. Replace:

```python
    parts = [
        _team_share_row(name, repo, layers),
        _cell("activity", "Activity", None, name, layers),
    ]
```

with:

```python
    parts = [
        _team_share_row(name, repo, layers),
        _trend_row(name, repo, layers),
        _cell("activity", "Activity", None, name, layers),
    ]
```

- [ ] **Step 5: Add CSS for `.trend-row` in `_CSS`**

Locate the `_CSS` string in `gh_contributions/report.py`. Add these rules directly after the `.pie-empty` rule:

```css
.trend-row { display: flex; flex-direction: row; flex-wrap: wrap; gap: 16px; }
.cell-trend { flex: 1 1 240px; max-width: 320px; }
.cell-trend canvas { max-height: 180px; width: 100% !important; height: 180px !important; }
.trend-empty { color: #888; text-align: center; padding: 24px 12px; }
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `python3 -m pytest tests/test_report.py::test_render_includes_trend_row_with_three_canvases tests/test_report.py::test_render_trend_row_shows_empty_cell_when_layer_has_no_data -v`
Expected: PASS.

- [ ] **Step 7: Run the full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add gh_contributions/report.py tests/test_report.py
git commit -m "feat(report): add .trend-row HTML container + CSS"
```

---

## Task 5: `_APP_JS` — implement `team_share_trend` render branch

Adds a `kind === 'team_share_trend'` branch in the existing canvas-iteration loop inside `_APP_JS`. Renders a Chart.js line chart per layer with a dashed reference line at `aggregate_share`, `0–100 %` y-axis, month-name x-labels, and a tooltip that shows `<Month YYYY> — <share>% (team X of total N)`. No unit tests — verified manually against the current cached run.

**Files:**
- Modify: `gh_contributions/report.py`

**Interfaces:**
- Consumes: from Task 3, each repo has `team_share_trend = {months, commits: {...}, pr: {...}, comments: {...}}`.
- Consumes: from Task 4, `<canvas data-chart="team_share_trend" data-repo="…" data-layer="…">` elements exist in the DOM. Empty layers get a `.trend-empty` div instead of a canvas, so the JS never sees them.
- Produces: rendered line chart per canvas. No new payload keys or DOM interfaces.

- [ ] **Step 1: Add `team_share_trend` render branch inside `_APP_JS`**

Locate the block in `_APP_JS`:

```js
document.querySelectorAll('canvas[data-chart]').forEach(function(canvas){
    const repo = data.repos[canvas.dataset.repo];
    if (!repo || repo.error) return;
    const kind = canvas.dataset.chart;

    if (kind === 'team_share' && repo.team_share) { ... }

    if (kind === 'activity' && repo.activity) { ... }
});
```

Insert this new branch between the two existing branches:

```js
    if (kind === 'team_share_trend' && repo.team_share_trend) {
      const trend = repo.team_share_trend;
      const layer = canvas.dataset.layer;
      const layerData = trend[layer];
      if (!layerData) return;

      const monthShort = { '01':'Jan','02':'Feb','03':'Mar','04':'Apr','05':'May','06':'Jun',
                            '07':'Jul','08':'Aug','09':'Sep','10':'Oct','11':'Nov','12':'Dec' };
      const labels = trend.months.map(function(m){
        const parts = m.split('-'); return monthShort[parts[1]] || m;
      });
      const sharePct = layerData.share.map(function(v){ return v == null ? null : v * 100; });
      const agg = layerData.aggregate_share;

      const datasets = [{
        label: layerLabels[layer] + ' share',
        data: sharePct,
        borderColor: color(layerIndex[layer]),
        backgroundColor: color(layerIndex[layer]),
        spanGaps: false,
        tension: 0,
        pointRadius: 3,
      }];
      if (agg != null) {
        datasets.push({
          label: 'window aggregate',
          data: labels.map(function(){ return agg * 100; }),
          borderColor: '#9ca3af',
          borderDash: [4, 4],
          borderWidth: 1,
          pointRadius: 0,
          fill: false,
        });
      }

      function trendTooltipTitle(ctxs) {
        return ctxs.length ? trend.months[ctxs[0].dataIndex] : '';
      }
      function trendTooltipLabel(ctx) {
        // Reference-line dataset: hide.
        if (ctx.datasetIndex === 1) return null;
        const i = ctx.dataIndex;
        const v = sharePct[i];
        if (v == null) return 'no data';
        return v.toFixed(1) + '% (team ' + layerData.team[i] + ' of total ' + layerData.total[i] + ')';
      }

      new Chart(canvas, {
        type: 'line',
        data: { labels: labels, datasets: datasets },
        options: {
          maintainAspectRatio: false,
          scales: {
            y: {
              min: 0, max: 100,
              ticks: { stepSize: 25, callback: function(v){ return v + '%'; } },
            },
          },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                title: trendTooltipTitle,
                label: trendTooltipLabel,
              },
            },
          },
        },
      });
    }
```

Notes:
- `layerLabels`, `layerIndex`, `color`, and `palette` already exist earlier in `_APP_JS`; reuse them directly.
- Reference-line dataset is intentionally omitted from the legend and its tooltip label returns `null` so it's not shown in the hover popup.
- If `trend.months.length === 1`, Chart.js draws a single point; the reference line still renders as a horizontal segment. Acceptable.

- [ ] **Step 2: Regenerate the report against the existing cache and eyeball it**

Run: `python3 -m gh_contributions.report out/2026-07-02T123016Z`

(No `GITHUB_TOKEN` needed — `report.py` reads `metrics.json` off disk; no API calls.)

Expected: `wrote out/2026-07-02T123016Z/report.html`.

Open the file in a browser:

- Three trend charts appear in a row directly under the three donuts.
- Each has a dashed gray horizontal reference line at the donut's percentage.
- Months on the x-axis: `Jan Feb Mar Apr May Jun`.
- Y-axis: 0% – 100% in 25% increments.
- Hovering a point shows `Jan 2026` (from `trend.months[i]`), `X.X% (team T of total N)`.
- Both the "All repos" tab and the per-repo tab render correctly.
- If any layer had `share: null` for a month, there is a visible gap in the line at that x-position.

- [ ] **Step 3: Run the full test suite one more time**

Run: `python3 -m pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add gh_contributions/report.py
git commit -m "feat(report): render team share monthly trend line charts"
```

---

## Self-Review Notes

- **Spec coverage:** All spec sections are covered — data flow (Task 1), payload shape in metrics.json (Task 1) and in the report payload (Task 3), aggregation across repos (Task 2), layout with `.trend-row` beneath the donuts (Task 4), rendering with reference line and gap handling (Task 5), and testing (embedded in each task).
- **Fixture note:** The spec mentioned adding a second month bucket to `tests/fixtures/team_share/`. This plan uses `tmp_path`-based tests instead, matching the pattern of the existing `test_team_share_zero_denominator_is_null` and `test_team_share_pr_reviews_windowed` tests. This keeps the existing single-month happy-path fixture and its `test_team_share_happy_path` assertions untouched. The behavior tested is identical.
- **Empty-state cell in Task 4** intentionally uses the class combination `cell cell-trend trend-empty` — the test in Step 1 asserts on `'class="cell cell-trend trend-empty"'` verbatim; keep this attribute order when emitting the HTML.
- **Task 5 has no automated tests.** Manual verification against the existing `out/2026-07-02T123016Z/` cache is the acceptance criterion; the pytest suite still guards the Python data path end-to-end via Tasks 1–4.
