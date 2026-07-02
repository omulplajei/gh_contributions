# Team share pie charts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single 4-bar team-share chart at the top of each repo tab with a row of three doughnut charts (`Commits`, `PR activity`, `Comments`) that mirror the layer structure of the per-user activity chart directly below.

**Architecture:** Breaking change to the `team_share` block in `metrics.json`. New nested shape stores per-sub-metric `team` and `total` maps under three layer keys (`commits`, `pr`, `comments`), enabling tooltip parity with the per-user chart. `report.py` renders one pie per layer with the layer's palette color for the team slice; zero-total layers render as placeholders emitted server-side by Python. Existing per-user activity chart, details table, and truncation banners are unchanged.

**Tech Stack:** Python 3 (stdlib only), Chart.js UMD (vendored), no new dependencies.

## Global Constraints

- No new runtime dependencies. Chart.js remains at the vendored version in `gh_contributions/assets/chart.umd.min.js`.
- Pure Python is TDD'd; JS/CSS is smoke-tested manually. No JS test infra is added.
- `metrics.json` shape change is intentional and unversioned; there are no other consumers and existing `out/` runs are re-generated.
- Layer keys are exactly `commits`, `pr`, `comments`. Sub-metric names use the raw identifiers from the per-user `activity.breakdown` payload (`pull_requests_opened`, `pull_requests_merged`, `APPROVED`, `CHANGES_REQUESTED`, `COMMENTED`, `review_comments`, `pr_conversation_comments`, `issue_comments`). Display renaming stays in JS.
- Palette color assignment: `palette[0]` (blue) for `commits`, `palette[1]` (green) for `pr`, `palette[2]` (yellow) for `comments`, non-team slice always `#d1d5db`.

---

## Task 1: Fixture — add `prs_by_merged.json` to team_share fixture

Adds the file that Task 2's PR-merged counting will read. No functional change to metrics output yet (Task 2's code change is what starts reading it).

**Files:**
- Create: `tests/fixtures/team_share/raw/acme__api/prs_by_merged.json`
- Modify: `tests/fixtures/team_share/raw/acme__api/_meta.json`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: two PRs merged in-window — one by team (`alice`), one by non-team (`eve`) — for Task 2's PR-layer test.

- [ ] **Step 1: Create `tests/fixtures/team_share/raw/acme__api/prs_by_merged.json`**

```json
[
  {"number": 1, "user": {"login": "alice"}, "created_at": "2026-02-01T10:00:00Z"},
  {"number": 3, "user": {"login": "eve"},   "created_at": "2026-02-15T10:00:00Z"}
]
```

- [ ] **Step 2: Update `tests/fixtures/team_share/raw/acme__api/_meta.json`**

Add a `prs_by_merged` entry alongside the existing ones:

```json
{
  "commits":         {"total_count": 10, "truncated": true},
  "prs_by_created":  {"total_count": 2,  "truncated": false},
  "prs_by_merged":   {"total_count": 2,  "truncated": false},
  "prs_updated":     {"total_count": 2,  "truncated": false},
  "reviews":         {"total_count": 3,  "truncated": false},
  "review_comments": {"total_count": 2,  "truncated": false},
  "issue_comments":  {"total_count": 2,  "truncated": false}
}
```

- [ ] **Step 3: Run existing tests to verify nothing regressed**

Run: `python3 -m pytest tests/ -x -q`
Expected: all tests pass (metrics.py doesn't read `prs_by_merged.json` yet, so adding the file is inert).

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/team_share/raw/acme__api/prs_by_merged.json tests/fixtures/team_share/raw/acme__api/_meta.json
git commit -m "test: add prs_by_merged fixture for team_share tests"
```

---

## Task 2: `metrics.py` — new `_apply_team_share` nested shape

Rewrites `_apply_team_share` to emit the three-layer nested structure with per-sub-metric team/total maps, including merged PRs and the PR-conv/issue-comment split. Removes the now-unused `_bucket` helper.

**Files:**
- Modify: `gh_contributions/metrics.py`
- Modify: `tests/test_metrics.py`

**Interfaces:**
- Consumes: `tests/fixtures/team_share/raw/acme__api/prs_by_merged.json` from Task 1.
- Produces: `metrics.json` `team_share` block for each repo with signature:
  ```python
  {
      "commits":  {"team": dict[str,int], "total": dict[str,int], "share": float | None},
      "pr":       {"team": dict[str,int], "total": dict[str,int], "share": float | None},
      "comments": {"team": dict[str,int], "total": dict[str,int], "share": float | None},
  }
  ```
  Sub-metric keys per layer (fixed order): `commits` → `("commits",)`; `pr` → `("pull_requests_opened", "pull_requests_merged", "APPROVED", "CHANGES_REQUESTED", "COMMENTED")`; `comments` → `("review_comments", "pr_conversation_comments", "issue_comments")`. All sub-keys always present, even at value 0.

- [ ] **Step 1: Write failing test for the new happy-path shape (replaces old happy-path test)**

Replace the existing `test_team_share_happy_path` in `tests/test_metrics.py`. Fixture recap:
- Commits: 4 team (`alice` x2, `bob` x2) + 6 non-team (`eve` x3, `dependabot[bot]` x3) = 10 total.
- PRs opened: `alice` (team) + `eve` (non-team) = 1 team / 2 total.
- PRs merged (new fixture): `alice` (team) + `eve` (non-team) = 1 team / 2 total.
- Reviews (in-window, counted states): PR 1 has `bob` APPROVED (team) + `eve` APPROVED (non-team); PR 2 has `alice` APPROVED (team). Team = 2 APPROVED / Total = 3 APPROVED. Others all zero.
- Review comments: `alice` + `eve` = 1 team / 2 total.
- Issue comments: `alice` on issue 1 (parent 1 is a known PR → `pr_conversation_comments`), `bob` on issue 99 (unknown → `issue_comments`). Team split: `pr_conversation_comments` = 1/1, `issue_comments` = 1/1.

```python
def test_team_share_happy_path() -> None:
    out = _load("team_share")
    share = out["repos"]["acme/api"]["team_share"]

    assert set(share) == {"commits", "pr", "comments"}

    assert share["commits"] == {
        "team":  {"commits": 4},
        "total": {"commits": 10},
        "share": pytest.approx(4 / 10),
    }

    assert share["pr"]["team"] == {
        "pull_requests_opened": 1,
        "pull_requests_merged": 1,
        "APPROVED":             2,
        "CHANGES_REQUESTED":    0,
        "COMMENTED":            0,
    }
    assert share["pr"]["total"] == {
        "pull_requests_opened": 2,
        "pull_requests_merged": 2,
        "APPROVED":             3,
        "CHANGES_REQUESTED":    0,
        "COMMENTED":            0,
    }
    assert share["pr"]["share"] == pytest.approx((1 + 1 + 2) / (2 + 2 + 3))

    assert share["comments"]["team"] == {
        "review_comments":          1,
        "pr_conversation_comments": 1,
        "issue_comments":           1,
    }
    assert share["comments"]["total"] == {
        "review_comments":          2,
        "pr_conversation_comments": 1,
        "issue_comments":           1,
    }
    assert share["comments"]["share"] == pytest.approx((1 + 1 + 1) / (2 + 1 + 1))
```

- [ ] **Step 2: Rewrite the zero-denominator test**

Replace `test_team_share_zero_denominator_is_null` in `tests/test_metrics.py`:

```python
def test_team_share_zero_denominator_is_null(tmp_path) -> None:
    from gh_contributions.config import Config
    from datetime import date

    cfg = Config(
        usernames=["alice"],
        repos=["acme/api"],
        since=date(2026, 1, 1),
        until=date(2026, 6, 30),
        metrics=["team_share"],
    )
    repo_dir = tmp_path / "acme__api"
    repo_dir.mkdir()
    (repo_dir / "_meta.json").write_text("{}")
    for f in ("commits.json", "prs_by_created.json", "prs_by_merged.json",
              "prs_updated.json", "review_comments.json", "issue_comments.json"):
        (repo_dir / f).write_text("[]")
    (repo_dir / "reviews").mkdir()

    out = compute(tmp_path, cfg)
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
```

- [ ] **Step 3: Run the two updated tests to verify they fail**

Run: `python3 -m pytest tests/test_metrics.py::test_team_share_happy_path tests/test_metrics.py::test_team_share_zero_denominator_is_null -v`
Expected: FAIL with `KeyError: 'pr'` / assertion errors about the old flat keys.

- [ ] **Step 4: Rewrite `_apply_team_share` in `gh_contributions/metrics.py`**

Add this constant near the top of the file, after `_REVIEW_STATES`:

```python
_TEAM_SHARE_SUB_METRICS = {
    "commits":  ("commits",),
    "pr":       ("pull_requests_opened", "pull_requests_merged",
                 "APPROVED", "CHANGES_REQUESTED", "COMMENTED"),
    "comments": ("review_comments", "pr_conversation_comments", "issue_comments"),
}
```

Replace the entire `_apply_team_share` function and delete the now-unused `_bucket` helper:

```python
def _apply_team_share(repo_dir: Path, config: Config, out: dict) -> None:
    team = set(config.usernames)
    lo, hi = _window_bounds(config)

    # commits — search results are already window-filtered by the query.
    commits_team = 0
    commits_total = 0
    for c in _read_json(repo_dir / "commits.json", default=[]):
        commits_total += 1
        if _author_login(c, "commits.json") in team:
            commits_team += 1

    # PR opened — window-filtered by search query.
    opened_team, opened_total = 0, 0
    for p in _read_json(repo_dir / "prs_by_created.json", default=[]):
        opened_total += 1
        if _author_login(p, "prs_by_created.json") in team:
            opened_team += 1

    # PR merged — window-filtered by search query.
    merged_team, merged_total = 0, 0
    for p in _read_json(repo_dir / "prs_by_merged.json", default=[]):
        merged_total += 1
        if _author_login(p, "prs_by_merged.json") in team:
            merged_team += 1

    # Reviews by state, windowed.
    rev_team = {s: 0 for s in _REVIEW_STATES}
    rev_total = {s: 0 for s in _REVIEW_STATES}
    reviews_dir = repo_dir / "reviews"
    if reviews_dir.is_dir():
        for review_file in sorted(reviews_dir.glob("*.json")):
            for r in _read_json(review_file, default=[]):
                state = r.get("state")
                if state not in _REVIEW_STATES:
                    continue
                if not _in_window(r.get("submitted_at"), lo, hi):
                    continue
                rev_total[state] += 1
                if ((r.get("user") or {}).get("login")) in team:
                    rev_team[state] += 1

    # Review comments (inline PR review comments), windowed.
    rc_team, rc_total = 0, 0
    for c in _read_json(repo_dir / "review_comments.json", default=[]):
        if not _in_window(c.get("created_at"), lo, hi):
            continue
        rc_total += 1
        if ((c.get("user") or {}).get("login")) in team:
            rc_team += 1

    # Issue comments: split by parent number against prs_updated.
    prs_updated = _read_json(repo_dir / "prs_updated.json", default=[])
    known_pr_numbers = {
        p.get("number") for p in prs_updated if isinstance(p.get("number"), int)
    }
    prc_team, prc_total = 0, 0
    ic_team, ic_total = 0, 0
    for c in _read_json(repo_dir / "issue_comments.json", default=[]):
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

Delete the old `_bucket` helper (currently at the bottom of the file):

```python
def _bucket(team_n: int, total_n: int) -> dict:
    return {
        "team": team_n,
        "total": total_n,
        "share": (team_n / total_n) if total_n else None,
    }
```

- [ ] **Step 5: Run the two updated tests to verify they pass**

Run: `python3 -m pytest tests/test_metrics.py::test_team_share_happy_path tests/test_metrics.py::test_team_share_zero_denominator_is_null -v`
Expected: PASS.

- [ ] **Step 6: Write failing test — PR reviews outside window are excluded**

Add to `tests/test_metrics.py` (after `test_team_share_zero_denominator_is_null`):

```python
def test_team_share_pr_reviews_windowed(tmp_path) -> None:
    from gh_contributions.config import Config
    from datetime import date
    import json as _json

    cfg = Config(
        usernames=["alice"],
        repos=["acme/api"],
        since=date(2026, 1, 1),
        until=date(2026, 6, 30),
        metrics=["team_share"],
    )
    repo_dir = tmp_path / "acme__api"
    repo_dir.mkdir()
    (repo_dir / "_meta.json").write_text("{}")
    for f in ("commits.json", "prs_by_created.json", "prs_by_merged.json",
              "prs_updated.json", "review_comments.json", "issue_comments.json"):
        (repo_dir / f).write_text("[]")
    (repo_dir / "reviews").mkdir()
    # Two reviews: one in-window (counted), one before window (excluded from BOTH team and total).
    (repo_dir / "reviews" / "1.json").write_text(_json.dumps([
        {"user": {"login": "alice"}, "state": "APPROVED", "submitted_at": "2026-02-10T10:00:00Z"},
        {"user": {"login": "eve"},   "state": "APPROVED", "submitted_at": "2025-12-31T10:00:00Z"},
    ]))

    share = compute(tmp_path, cfg)["repos"]["acme/api"]["team_share"]
    assert share["pr"]["team"]["APPROVED"]  == 1
    assert share["pr"]["total"]["APPROVED"] == 1
```

- [ ] **Step 7: Run the new test to verify it passes**

Run: `python3 -m pytest tests/test_metrics.py::test_team_share_pr_reviews_windowed -v`
Expected: PASS (the implementation already applies `_in_window`).

- [ ] **Step 8: Run the full metrics test file**

Run: `python3 -m pytest tests/test_metrics.py -v`
Expected: all `test_metrics.py` tests PASS. (`report.py` tests will still fail — Task 3 fixes them.)

- [ ] **Step 9: Commit**

```bash
git add gh_contributions/metrics.py tests/test_metrics.py
git commit -m "feat(metrics): nested team_share shape for 3-pie report"
```

---

## Task 3: `report.py` — new `_aggregate` team_share + `_ts()` test helper

Updates `_aggregate` to sum per-sub-metric team/total across repos under the new three-layer shape, and updates the `_ts()` test helper in `tests/test_report.py` to produce the new shape.

**Files:**
- Modify: `gh_contributions/report.py`
- Modify: `tests/test_report.py`

**Interfaces:**
- Consumes: `metrics.json` `team_share` shape from Task 2 (`{commits, pr, comments}`, each with `{team: dict, total: dict, share: float | None}`).
- Produces: aggregate `team_share` block with the same nested shape. Empty `team` / `total` sub-dicts are pre-populated with all expected sub-metric keys at value 0. Share re-derived from summed sub-totals.

- [ ] **Step 1: Update the `_ts()` helper in `tests/test_report.py`**

Replace the existing `_ts` function:

```python
_TEAM_SHARE_SUB_METRICS = {
    "commits":  ("commits",),
    "pr":       ("pull_requests_opened", "pull_requests_merged",
                 "APPROVED", "CHANGES_REQUESTED", "COMMENTED"),
    "comments": ("review_comments", "pr_conversation_comments", "issue_comments"),
}


def _ts(commits=(0, 0), pr=(0, 0), comments=(0, 0)):
    """Build a new-shape team_share block. Puts the (team, total) totals in
    the first sub-key of each layer; other sub-keys are 0."""
    def layer(pair, sub_keys):
        team_t, total_t = pair
        team_map  = {k: 0 for k in sub_keys}
        total_map = {k: 0 for k in sub_keys}
        team_map[sub_keys[0]]  = team_t
        total_map[sub_keys[0]] = total_t
        return {
            "team":  team_map,
            "total": total_map,
            "share": (team_t / total_t) if total_t else None,
        }
    return {
        "commits":  layer(commits,  _TEAM_SHARE_SUB_METRICS["commits"]),
        "pr":       layer(pr,       _TEAM_SHARE_SUB_METRICS["pr"]),
        "comments": layer(comments, _TEAM_SHARE_SUB_METRICS["comments"]),
    }
```

- [ ] **Step 2: Update `_aggregate` tests to the new shape**

In `tests/test_report.py`, rewrite `test_aggregate_recomputes_team_share_ratios`:

```python
def test_aggregate_recomputes_team_share_ratios() -> None:
    metrics = _metrics({
        "acme/api": _repo(ts=_ts(commits=(7, 10))),
        "acme/web": _repo(ts=_ts(commits=(3, 5))),
    })
    agg = _aggregate(metrics)
    assert agg["team_share"]["commits"] == {
        "team":  {"commits": 10},
        "total": {"commits": 15},
        "share": pytest.approx(10 / 15),
    }
    # pr and comments were zero on both repos -> summed to all-zero sub-maps and share=None.
    assert agg["team_share"]["pr"]["share"] is None
    assert agg["team_share"]["pr"]["team"]["APPROVED"] == 0
    assert agg["team_share"]["pr"]["total"]["pull_requests_opened"] == 0
    assert agg["team_share"]["comments"]["share"] is None
```

Rewrite `test_aggregate_skips_errored_repos_but_uses_healthy_ones` (only the assertion changes):

```python
def test_aggregate_skips_errored_repos_but_uses_healthy_ones() -> None:
    metrics = _metrics({
        "broken":   _repo(error="not_found"),
        "acme/web": _repo(
            commits_by_user={"alice": {"commits": 4}},
            ts=_ts(commits=(4, 8)),
        ),
    })
    agg = _aggregate(metrics)
    assert agg is not None
    assert agg["per_user"]["alice"]["authoring"]["commits"] == 4
    assert agg["team_share"]["commits"] == {
        "team":  {"commits": 4},
        "total": {"commits": 8},
        "share": 0.5,
    }
```

- [ ] **Step 3: Add a new test asserting sub-metric-wise summation across repos**

Add to `tests/test_report.py` in the `_aggregate` section:

```python
def test_aggregate_sums_team_share_sub_metrics_per_layer() -> None:
    # Two repos with different sub-metric mixes in the pr layer.
    def repo_with_pr(opened_team, opened_total, approved_team, approved_total):
        pr = {
            "team": {
                "pull_requests_opened": opened_team,
                "pull_requests_merged": 0,
                "APPROVED":             approved_team,
                "CHANGES_REQUESTED":    0,
                "COMMENTED":            0,
            },
            "total": {
                "pull_requests_opened": opened_total,
                "pull_requests_merged": 0,
                "APPROVED":             approved_total,
                "CHANGES_REQUESTED":    0,
                "COMMENTED":            0,
            },
            "share": ((opened_team + approved_team)
                     / (opened_total + approved_total)) if (opened_total + approved_total) else None,
        }
        return {
            "per_user": {},
            "team_share": {"commits": _ts()["commits"], "pr": pr, "comments": _ts()["comments"]},
            "truncated": {},
            "error": None,
        }

    metrics = _metrics({
        "acme/api": repo_with_pr(2, 5, 3, 4),
        "acme/web": repo_with_pr(1, 3, 5, 6),
    })
    agg = _aggregate(metrics)
    pr = agg["team_share"]["pr"]
    assert pr["team"]  == {"pull_requests_opened": 3, "pull_requests_merged": 0,
                            "APPROVED": 8, "CHANGES_REQUESTED": 0, "COMMENTED": 0}
    assert pr["total"] == {"pull_requests_opened": 8, "pull_requests_merged": 0,
                            "APPROVED": 10, "CHANGES_REQUESTED": 0, "COMMENTED": 0}
    assert pr["share"] == pytest.approx((3 + 8) / (8 + 10))
```

- [ ] **Step 4: Run the aggregate tests to verify they fail with the old code**

Run: `python3 -m pytest tests/test_report.py -k aggregate -v`
Expected: FAIL on the three updated/new aggregate tests (old `_aggregate` still writes flat shape via the removed `_TEAM_SHARE_BUCKETS`).

- [ ] **Step 5: Rewrite `_aggregate.team_share` in `gh_contributions/report.py`**

At the top of `gh_contributions/report.py`, replace the `_TEAM_SHARE_BUCKETS` constant with the new sub-metrics map (keep other constants unchanged):

```python
_TEAM_SHARE_SUB_METRICS = {
    "commits":  ("commits",),
    "pr":       ("pull_requests_opened", "pull_requests_merged",
                 "APPROVED", "CHANGES_REQUESTED", "COMMENTED"),
    "comments": ("review_comments", "pr_conversation_comments", "issue_comments"),
}
```

In `_aggregate`, replace the team-share section:

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
            team_share[layer] = {"team": team, "total": total, "share": (t / n) if n else None}
```

- [ ] **Step 6: Run aggregate tests again to verify they pass**

Run: `python3 -m pytest tests/test_report.py -k aggregate -v`
Expected: all `_aggregate` tests PASS. (Render / payload tests still fail — Task 4 fixes them.)

- [ ] **Step 7: Commit**

```bash
git add gh_contributions/report.py tests/test_report.py
git commit -m "feat(report): aggregate team_share sub-metrics per layer"
```

---

## Task 4: `report.py` — new `_chart_data.team_share` payload

Emits the payload block the JS renderer will consume: parallel arrays `layers`, `shares`, `team`, `total` plus a `breakdown` map of sub-metric team/total per layer.

**Files:**
- Modify: `gh_contributions/report.py`
- Modify: `tests/test_report.py`

**Interfaces:**
- Consumes: raw `repo["team_share"]` nested shape from Task 2/3.
- Produces: per-repo chart-data `team_share` block:
  ```python
  {
      "layers":   ["commits", "pr", "comments"],           # fixed order
      "shares":   [float | None, float | None, float | None],
      "team":     [int, int, int],                          # per-layer sum of team sub-metrics
      "total":    [int, int, int],                          # per-layer sum of total sub-metrics
      "breakdown": {
          "commits":  {"team": dict[str,int], "total": dict[str,int]},
          "pr":       {"team": dict[str,int], "total": dict[str,int]},
          "comments": {"team": dict[str,int], "total": dict[str,int]},
      },
  }
  ```

- [ ] **Step 1: Rewrite the render payload test for team_share shape**

Replace `test_render_embeds_report_data_payload` in `tests/test_report.py`. Also delete the now-unused `_TEAM_SHARE_BUCKETS_EXPECTED` module-level tuple below the test.

```python
def test_render_embeds_report_data_payload() -> None:
    metrics = _metrics({
        "acme/api": _repo(
            commits_by_user={
                "alice": {"commits": 5, "pull_requests_opened": 2, "COMMENTED": 3, "review_comments": 4},
                "bob":   {"commits": 2, "APPROVED": 1, "issue_comments": 1},
            },
            ts=_ts(commits=(7, 10), pr=(6, 9), comments=(5, 8)),
        ),
    })
    html = render(metrics)
    payload = _find_payload(html)

    assert list(payload["repos"]) == ["acme/api"]
    repo = payload["repos"]["acme/api"]
    assert repo["error"] is None

    ts = repo["team_share"]
    assert ts["layers"] == ["commits", "pr", "comments"]
    assert ts["team"]   == [7, 6, 5]
    assert ts["total"]  == [10, 9, 8]
    assert ts["shares"] == [pytest.approx(0.7), pytest.approx(6 / 9), pytest.approx(5 / 8)]

    # Breakdown: sub-metric maps present per layer.
    assert set(ts["breakdown"]) == {"commits", "pr", "comments"}
    assert set(ts["breakdown"]["pr"]) == {"team", "total"}
    assert set(ts["breakdown"]["pr"]["team"]) == {
        "pull_requests_opened", "pull_requests_merged",
        "APPROVED", "CHANGES_REQUESTED", "COMMENTED",
    }
    # _ts() puts all counts in the first sub-key.
    assert ts["breakdown"]["pr"]["team"]["pull_requests_opened"] == 6
    assert ts["breakdown"]["pr"]["team"]["APPROVED"] == 0
    assert ts["breakdown"]["pr"]["total"]["pull_requests_opened"] == 9

    # activity block: users sorted by total desc — unchanged.
    assert repo["activity"]["users"] == ["alice", "bob"]
    assert repo["activity"]["totals"] == [14, 4]
    assert repo["activity"]["layers"]["commits"] == [5, 2]

    # Removed payload keys must not appear.
    assert "authoring" not in repo
    assert "reviews" not in repo
    assert "comments" not in repo
```

Also delete this line (elsewhere in the file):

```python
_TEAM_SHARE_BUCKETS_EXPECTED = ("commits", "pull_requests_opened", "reviews_given", "comments")
```

- [ ] **Step 2: Add invariants test**

Add to `tests/test_report.py`:

```python
def test_render_team_share_invariants() -> None:
    metrics = _metrics({
        "acme/api": _repo(ts=_ts(commits=(3, 4), pr=(2, 10), comments=(0, 0))),
    })
    ts = _find_payload(render(metrics))["repos"]["acme/api"]["team_share"]

    for i, layer in enumerate(ts["layers"]):
        team_sum  = sum(ts["breakdown"][layer]["team"].values())
        total_sum = sum(ts["breakdown"][layer]["total"].values())
        assert team_sum  == ts["team"][i]
        assert total_sum == ts["total"][i]
        if total_sum == 0:
            assert ts["shares"][i] is None
        else:
            assert ts["shares"][i] == pytest.approx(team_sum / total_sum)
```

- [ ] **Step 3: Run the two tests to verify they fail**

Run: `python3 -m pytest tests/test_report.py::test_render_embeds_report_data_payload tests/test_report.py::test_render_team_share_invariants -v`
Expected: FAIL — `_chart_data` still writes the old `{buckets, team, total, share}` list-form.

- [ ] **Step 4: Rewrite the team_share section of `_chart_data`**

In `gh_contributions/report.py`, replace the block starting at `if "team_share" in layers and repo.get("team_share"):` inside `_chart_data`:

```python
    if "team_share" in layers and repo.get("team_share"):
        ts = repo["team_share"]
        layers_list = list(_TEAM_SHARE_SUB_METRICS)   # ("commits", "pr", "comments")
        result["team_share"] = {
            "layers":    layers_list,
            "shares":    [ts[l]["share"] for l in layers_list],
            "team":      [sum(ts[l]["team"].values())  for l in layers_list],
            "total":     [sum(ts[l]["total"].values()) for l in layers_list],
            "breakdown": {
                l: {"team": ts[l]["team"], "total": ts[l]["total"]}
                for l in layers_list
            },
        }
```

- [ ] **Step 5: Run the two tests to verify they pass**

Run: `python3 -m pytest tests/test_report.py::test_render_embeds_report_data_payload tests/test_report.py::test_render_team_share_invariants -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gh_contributions/report.py tests/test_report.py
git commit -m "feat(report): emit per-layer team_share chart payload"
```

---

## Task 5: `report.py` — `_tab_body` + `_team_share_row` + `_CSS` (3-pie layout)

Replaces the single team_share cell with a three-pie row emitted server-side. When the `team_share` layer is disabled in config the whole row becomes a placeholder card; when a specific layer's share is `None` (zero denominator) that pie becomes a per-cell placeholder card.

**Files:**
- Modify: `gh_contributions/report.py`
- Modify: `tests/test_report.py`

**Interfaces:**
- Consumes: raw `repo["team_share"]` nested shape (Task 2/3), the set of `layers` currently enabled in config.
- Produces:
  - When `team_share` layer disabled: one wide `.cell.layer-disabled` card in place of the row.
  - Otherwise: a `<div class="team-share-row">` containing three cells, each either
    - `<div class="cell cell-pie"><canvas data-chart="team_share" data-repo="<repo>" data-layer="<layer>"></canvas></div>` when the layer's `share` is not None, or
    - `<div class="cell cell-pie pie-empty"><strong>{layer title}</strong><p>no data in window</p></div>` when `share is None`.
- `_LAYER_TITLE` constant added: `{"commits": "Commits", "pr": "PR activity", "comments": "Comments"}`.

- [ ] **Step 1: Update the error-repo test's canvas count**

`test_render_errored_repo_shows_error_banner_in_its_tab` in `tests/test_report.py` currently asserts `healthy_section.count("<canvas") == 2`. Change to `4` (three team_share pies + one activity chart) and change `_repo(ts=_ts())` for the `healthy` entry so all shares are `None` — wait, we want non-null shares so all three canvases render. Use `_ts(commits=(1, 2), pr=(1, 2), comments=(1, 2))`:

```python
def test_render_errored_repo_shows_error_banner_in_its_tab() -> None:
    metrics = _metrics({
        "healthy": _repo(ts=_ts(commits=(1, 2), pr=(1, 2), comments=(1, 2))),
        "broken":  _repo(error="not_found"),
    })
    html = render(metrics)
    broken_section = _extract_section(html, "broken")
    assert "error-banner" in broken_section
    assert "not_found" in broken_section
    assert "<canvas" not in broken_section
    healthy_section = _extract_section(html, "healthy")
    assert "error-banner" not in healthy_section
    assert healthy_section.count("<canvas") == 4  # 3 team_share pies + 1 activity
```

- [ ] **Step 2: Add a test for the zero-denominator pie placeholder**

Add to `tests/test_report.py`:

```python
def test_render_zero_total_layer_shows_pie_placeholder() -> None:
    # commits has data, pr and comments are both zero -> two of the three pies
    # render as placeholder cards, one as a canvas.
    metrics = _metrics({
        "acme/api": _repo(ts=_ts(commits=(3, 5), pr=(0, 0), comments=(0, 0))),
    })
    html = render(metrics)
    section = _extract_section(html, "acme/api")

    # Only the commits pie renders as a canvas.
    assert section.count('data-chart="team_share"') == 1
    assert 'data-layer="commits"' in section
    assert 'data-layer="pr"' not in section
    assert 'data-layer="comments"' not in section

    # Placeholder card text present for the two empty layers.
    assert section.count("pie-empty") == 2
    assert section.count("no data in window") == 2
```

- [ ] **Step 3: Add a test for `team_share` layer disabled in config**

The existing `test_render_layer_disabled_placeholder` covers this case at a high level (`section.count("layer-disabled") == 1`). Verify it still fits the new layout by extending it:

Replace `test_render_layer_disabled_placeholder` in `tests/test_report.py`:

```python
def test_render_layer_disabled_placeholder() -> None:
    # Only 'authoring' enabled -> team_share row is one wide placeholder card,
    # activity cell always renders.
    metrics = _metrics(
        {"acme/api": _repo(commits_by_user={"alice": {"commits": 1}}, ts=_ts())},
        layers=("authoring",),
    )
    html = render(metrics)
    section = _extract_section(html, "acme/api")
    assert 'data-chart="activity"' in section
    assert 'data-chart="team_share"' not in section
    assert section.count("layer-disabled") == 1  # single wide row placeholder
    assert "team-share-row" not in section        # row wrapper not emitted when disabled
```

- [ ] **Step 4: Run the three new/updated tests to verify they fail**

Run: `python3 -m pytest tests/test_report.py::test_render_errored_repo_shows_error_banner_in_its_tab tests/test_report.py::test_render_zero_total_layer_shows_pie_placeholder tests/test_report.py::test_render_layer_disabled_placeholder -v`
Expected: FAIL — current `_tab_body` emits only 2 canvases and no `team-share-row` markup.

- [ ] **Step 5: Rewrite `_tab_body` and add `_team_share_row` in `gh_contributions/report.py`**

Add this constant near the other module-level constants at the top:

```python
_LAYER_TITLE = {
    "commits":  "Commits",
    "pr":       "PR activity",
    "comments": "Comments",
}
```

Replace `_tab_body`:

```python
def _tab_body(name: str, repo: dict, layers: set, active: bool) -> str:
    label = "All repos" if name == "__aggregate__" else name
    hidden = "" if active else " hidden"
    if repo.get("error"):
        return (
            f'<section data-repo="{name}"{hidden}>'
            f'  <div class="error-banner">{_esc(label)}: {_esc(repo["error"])}</div>'
            f'</section>'
        )
    parts = [
        _team_share_row(name, repo, layers),
        _cell("activity", "Activity", None, name, layers),
    ]
    return (
        f'<section data-repo="{name}"{hidden}>'
        f'  <div class="stack">{"".join(parts)}</div>'
        f'  <table class="details" data-repo="{name}"></table>'
        f'</section>'
    )


def _team_share_row(repo_name: str, repo: dict, layers: set) -> str:
    if "team_share" not in layers:
        return (
            '<div class="cell layer-disabled">'
            '<strong>Team share</strong>'
            '<p>Layer <code>team_share</code> disabled in config.</p>'
            "</div>"
        )
    ts = repo.get("team_share") or {}
    pies: list[str] = []
    for layer in _TEAM_SHARE_SUB_METRICS:
        share = (ts.get(layer) or {}).get("share")
        title = _LAYER_TITLE[layer]
        if share is None:
            pies.append(
                '<div class="cell cell-pie pie-empty">'
                f'<strong>{_esc(title)}</strong>'
                '<p>no data in window</p>'
                "</div>"
            )
        else:
            pies.append(
                '<div class="cell cell-pie">'
                f'<canvas data-chart="team_share" data-repo="{repo_name}" data-layer="{layer}"></canvas>'
                "</div>"
            )
    return f'<div class="team-share-row">{"".join(pies)}</div>'
```

Also simplify `_cell` — it's now only ever called with `chart_key="activity"`. Delete the `cell-team-share` branch:

```python
def _cell(chart_key: str, title: str, required_layer: str | None, repo_name: str, layers: set) -> str:
    if required_layer is not None and required_layer not in layers:
        return (
            '<div class="cell layer-disabled">'
            f'<strong>{_esc(title)}</strong>'
            f'<p>Layer <code>{_esc(required_layer)}</code> disabled in config.</p>'
            "</div>"
        )
    extra_class = " cell-activity" if chart_key == "activity" else ""
    return (
        f'<div class="cell{extra_class}">'
        f'<canvas data-chart="{chart_key}" data-repo="{repo_name}"></canvas>'
        "</div>"
    )
```

- [ ] **Step 6: Update `_CSS`**

In `gh_contributions/report.py`, inside the `_CSS` triple-quoted string:

- Delete these two lines:
  ```
  .cell-team-share { max-width: 480px; }
  .cell-team-share canvas { max-height: 320px; }
  ```
- Add these lines (after the existing `.cell { ... }` rule):
  ```
  .team-share-row { display: flex; flex-direction: row; flex-wrap: wrap; gap: 16px; }
  .cell-pie { flex: 1 1 240px; max-width: 320px; }
  .cell-pie canvas { max-height: 260px; }
  .pie-empty { color: #888; text-align: center; padding: 24px 12px; }
  ```

- [ ] **Step 7: Run the three tests to verify they pass**

Run: `python3 -m pytest tests/test_report.py::test_render_errored_repo_shows_error_banner_in_its_tab tests/test_report.py::test_render_zero_total_layer_shows_pie_placeholder tests/test_report.py::test_render_layer_disabled_placeholder -v`
Expected: PASS.

- [ ] **Step 8: Run the full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: all tests PASS.

- [ ] **Step 9: Commit**

```bash
git add gh_contributions/report.py tests/test_report.py
git commit -m "feat(report): render team_share as row of three pies"
```

---

## Task 6: `report.py` — `_APP_JS` doughnut branch

Adds the `team_share` chart-kind branch to the client-side JS: doughnut chart per pie canvas with layer-matched team-slice color, per-user-style tooltip (sub-metric parenthetical, omitted for 0-count slices and for the single-sub-metric commits layer), and a title showing `"{Layer title} — X.X%"`. Automated tests already cover the HTML shape from Task 5; this task's deliverable is verified manually by opening the rendered `report.html`.

**Files:**
- Modify: `gh_contributions/report.py` (the `_APP_JS` string only)

**Interfaces:**
- Consumes: the `team_share` chart-data payload from Task 4 (`layers`, `shares`, `team`, `total`, `breakdown`) and the per-canvas `data-layer` attribute set in Task 5.
- Produces: rendered doughnut charts in the browser.

- [ ] **Step 1: Hoist shared display maps in `_APP_JS`**

Currently `displayNames` and `layerLabels` are defined inside the `kind === 'activity'` branch. Move them to the top of the outer IIFE, right after the `palette` / `color` helper, so both the activity branch and the new team_share branch can reference them. In `gh_contributions/report.py`, inside `_APP_JS`:

Replace this block near the top of the IIFE:

```js
  const palette = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#14b8a6'];
  function color(i){ return palette[i % palette.length]; }
```

with:

```js
  const palette = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#14b8a6'];
  function color(i){ return palette[i % palette.length]; }

  const displayNames = {
    commits: 'commits',
    pull_requests_opened: 'opened',
    pull_requests_merged: 'merged',
    APPROVED: 'approved',
    CHANGES_REQUESTED: 'changes',
    COMMENTED: 'commented',
    review_comments: 'review',
    pr_conversation_comments: 'PR conv',
    issue_comments: 'issue',
  };
  const layerLabels = { commits: 'Commits', pr: 'PR activity', comments: 'Comments' };
  const layerIndex  = { commits: 0, pr: 1, comments: 2 };
```

Then, inside the existing `kind === 'activity'` branch, delete the now-duplicate declarations of `displayNames` and `layerLabels`.

- [ ] **Step 2: Replace the existing `team_share` chart branch with the doughnut branch**

Currently `_APP_JS` has:

```js
    if (kind === 'team_share' && repo.team_share) {
      const ts = repo.team_share;
      new Chart(canvas, {
        type: 'bar',
        ...
      });
    }
```

Replace the whole `if (kind === 'team_share' ...) { ... }` block with:

```js
    if (kind === 'team_share' && repo.team_share) {
      const ts = repo.team_share;
      const layer = canvas.dataset.layer;
      const i = ts.layers.indexOf(layer);
      if (i < 0) return;

      const teamCount = ts.team[i];
      const total     = ts.total[i];
      const share     = ts.shares[i];
      if (share === null) return;  // server-side placeholder already rendered

      const bd      = ts.breakdown[layer];
      const teamBd  = bd.team;
      const totalBd = bd.total;
      const subKeys = Object.keys(totalBd);
      const sharePct = (share * 100).toFixed(1) + '%';

      function pieTooltipLabel(ctx) {
        const isTeam = ctx.dataIndex === 0;
        const sliceLabel = isTeam ? 'Team' : 'Non-team';
        const sliceCount = ctx.parsed;
        let base = sliceLabel + ': ' + sliceCount + ' / ' + total;
        if (sliceCount === 0 || subKeys.length <= 1) return base;
        const parts = subKeys.map(function(k){
          const v = isTeam ? teamBd[k] : (totalBd[k] - teamBd[k]);
          return v > 0 ? (displayNames[k] || k) + ' ' + v : null;
        }).filter(function(x){ return x !== null; });
        if (parts.length) base += ' (' + parts.join(', ') + ')';
        return base;
      }

      new Chart(canvas, {
        type: 'doughnut',
        data: {
          labels: ['Team', 'Non-team'],
          datasets: [{
            data: [teamCount, total - teamCount],
            backgroundColor: [color(layerIndex[layer]), '#d1d5db'],
            borderWidth: 1,
          }],
        },
        options: {
          plugins: {
            title:  { display: true, text: layerLabels[layer] + ' \u2014 ' + sharePct },
            legend: { position: 'bottom' },
            tooltip: { callbacks: { label: pieTooltipLabel } },
          },
        },
      });
    }
```

- [ ] **Step 3: Run the full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: all tests PASS (the JS changes don't affect Python assertions; the HTML still contains the expected canvases and structure from Task 5).

- [ ] **Step 4: Manual verification against a real run**

Pick the newest run directory under `out/` (any one where `metrics: [authoring, collaboration, team_share]` was set) and re-run `metrics.py` first so the on-disk `metrics.json` reflects the new shape, then re-render the report:

```bash
# Regenerate metrics.json against the existing raw pages.
python3 -c "from pathlib import Path; from gh_contributions.run import _run_metrics; import sys; run=Path(sorted(Path('out').iterdir())[-1]); _run_metrics(run)"
```

If the `_run_metrics` helper doesn't exist under that name, fall back to a full re-fetch: `python3 -m gh_contributions.run`. Then:

```bash
python3 -m gh_contributions.report
open "$(ls -td out/*/report.html | head -1)"
```

Expected visual result:
- Three doughnuts across the top of each repo tab, left-to-right: `Commits — X.X%` (blue), `PR activity — X.X%` (green), `Comments — X.X%` (yellow).
- Each doughnut legend shows `Team` / `Non-team`.
- Hover on the team slice of the `Commits` pie: tooltip reads `Team: <n> / <N>` (no parenthetical, single sub-metric).
- Hover on the team slice of `PR activity`: tooltip reads `Team: <n> / <N> (opened X, merged Y, approved Z, ...)` with zero sub-metrics omitted.
- Hover on the non-team slice: same format, values are `total - team` per sub-metric.
- A repo with zero comments in the window shows a `no data in window` card in place of the Comments pie; the other two pies still render.
- Setting `metrics: [authoring, collaboration]` (dropping `team_share`) collapses the top row to a single "Layer `team_share` disabled in config" card spanning the row.
- The per-user activity chart below is unchanged.

- [ ] **Step 5: Commit**

```bash
git add gh_contributions/report.py
git commit -m "feat(report): doughnut charts for team_share layers"
```

---

## Self-Review

**1. Spec coverage.** Every section of `docs/superpowers/specs/2026-07-02-team-share-pie-charts-design.md` maps to a task:

| Spec section | Task |
|---|---|
| Metric shape (metrics.json) — 3 layers, per-sub-metric team/total | Task 2 |
| Fixture: add `prs_by_merged.json` | Task 1 |
| `_apply_team_share` behavior (commits, PR, comments layers + PR-conv split) | Task 2 |
| Aggregation across repos (sum sub-metrics, recompute share) | Task 3 |
| `_chart_data` payload shape | Task 4 |
| Repo tab layout (3-pie row + activity + table) | Task 5 |
| CSS changes | Task 5 |
| `_tab_body` change / `_team_share_row` helper | Task 5 |
| JS chart config (doughnut branch, tooltip parity) | Task 6 |
| Zero-denominator layer placeholder | Task 5 (server-side) |
| `team_share` layer disabled → wide placeholder card | Task 5 |
| Aggregate tab uses same shape (no special-case) | Task 3 (via `_aggregate`) |
| Testing — metrics unit tests | Task 2 (test_metrics.py updates) |
| Testing — aggregate tests | Task 3 |
| Testing — chart_data shape/invariants tests | Task 4 |
| Testing — HTML layout / placeholder / layer-disabled tests | Task 5 |
| Manual verification checklist | Task 6, Step 4 |

**2. Placeholder scan.** Every code step contains actual code, every test step contains actual assertions, every command has an expected result. No "TBD", "add appropriate error handling", or "similar to Task N".

**3. Type consistency.** `_TEAM_SHARE_SUB_METRICS` is defined identically in `metrics.py` (Task 2), `report.py` (Task 3), and `test_report.py` (`_ts` helper, Task 3). `_LAYER_TITLE` in Python (Task 5) uses the same keys and values as `layerLabels` in JS (Task 6). Sub-metric names match exactly across all tasks: `pull_requests_opened`, `pull_requests_merged`, `APPROVED`, `CHANGES_REQUESTED`, `COMMENTED`, `review_comments`, `pr_conversation_comments`, `issue_comments`. `_team_share_row(repo_name, repo, layers)` signature is defined in Task 5 and matches the call site in the updated `_tab_body` in the same task.

---

## Execution Handoff

**Plan complete and saved to** `docs/superpowers/plans/2026-07-02-team-share-pie-charts.md`. **Two execution options:**

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
