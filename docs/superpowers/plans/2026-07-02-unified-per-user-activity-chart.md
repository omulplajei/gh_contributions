# Unified Per-User Activity Chart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three per-user charts in each repo tab of the HTML report with a single horizontal stacked bar chart showing every developer's total contributions broken into three layers: Commits, PR activity, Comments.

**Architecture:** Extend `_chart_data` in `gh_contributions/report.py` to emit a new pre-aggregated `activity` block per repo (users pre-sorted, layer sums, per-user breakdown for tooltips), then swap the tab body's chart cells and the JS render branches to consume it. Team share chart and details table are unchanged.

**Tech Stack:** Python stdlib only for the payload change. Existing vendored Chart.js 4.x for the new chart config (horizontal stacked bar via `indexAxis: 'y'`). No new dependencies or assets.

## Global Constraints

Copied verbatim from [unified per-user activity chart design spec](../specs/2026-07-02-unified-per-user-activity-chart-design.md):

- Three stack layers per user, exact composition fixed:
  - `commits` = `authoring.commits`
  - `pr` = `authoring.pull_requests_opened` + `authoring.pull_requests_merged` + `collaboration.reviews_given.APPROVED` + `collaboration.reviews_given.CHANGES_REQUESTED` + `collaboration.reviews_given.COMMENTED`
  - `comments` = `collaboration.review_comments` + `collaboration.pr_conversation_comments` + `collaboration.issue_comments`
- `authoring.issues_opened` and `collaboration.cross_team_reviews` are excluded from every stack layer but stay in `per_user_raw` (details table intact).
- Users pre-sorted by total contributions descending, tie-break alphabetical by login.
- Chart always renders all three layers; missing config layers just produce 0s. A client-side note above the chart names any missing config layer(s).
- When a repo has `error != null`, `activity` is omitted (same pattern as existing chart-data keys).
- Empty repo shape is `{"users": [], "totals": [], "layers": {"commits": [], "pr": [], "comments": []}, "breakdown": {}}`.
- `_aggregate` is not modified — it produces the same per-user shape as any single repo, so `_chart_data` runs on top of it unchanged.
- No feature flag or fallback to the old three-chart layout. Old cells, JS branches, and payload keys (`authoring`, `reviews`, `comments`) are deleted.
- No JS unit tests (no infrastructure). Manual verification: open the generated `report.html` in a browser after `python3 -m gh_contributions.report`.

## File Structure

Files this plan modifies:

| Path | Action | Responsibility |
|---|---|---|
| `gh_contributions/report.py` | Modify | Add `activity` block to `_chart_data`; swap `_tab_body` cells; rewrite JS render branch; update CSS; drop old payload keys and JS branches |
| `tests/test_report.py` | Modify | New tests for the `activity` block; update existing tests that reference removed payload keys / canvas counts / layer-disabled placeholders |

No new files. No changes to `metrics.py`, `_aggregate`, `config.py`, README, or vendored assets.

---

## Starting State

- HEAD is `1aa451d docs: spec for unified per-user activity chart` (this plan's spec, already committed).
- Working tree clean.
- All tests pass: `python3 -m pytest tests/ -q`.

Verify:

```bash
git log --oneline -1
python3 -m pytest tests/ -q
```

Expected: HEAD is `1aa451d`; pytest reports all passing.

---

### Task 1: Emit `activity` payload block from `_chart_data`

**Files:**
- Modify: `gh_contributions/report.py` (`_chart_data` — add new block; leave existing `authoring` / `reviews` / `comments` blocks in place for now)
- Modify: `tests/test_report.py` (add new tests)

**Interfaces:**
- Consumes: existing `per_user` dict-of-dicts already produced by `metrics.py` and preserved unchanged by `_aggregate`. Sub-keys used: `authoring.commits`, `authoring.pull_requests_opened`, `authoring.pull_requests_merged`, `collaboration.reviews_given.{APPROVED,CHANGES_REQUESTED,COMMENTED}`, `collaboration.review_comments`, `collaboration.pr_conversation_comments`, `collaboration.issue_comments`. All accesses use `.get(...)` chains to tolerate missing `authoring`/`collaboration` blocks.
- Produces: new `activity` key on each per-repo dict returned by `_chart_data`. Shape:
  ```python
  {
      "users":  list[str],                                # pre-sorted, total desc, login asc
      "totals": list[int],                                # sum of three layers at each index
      "layers": {                                         # each array parallel to users
          "commits":  list[int],
          "pr":       list[int],
          "comments": list[int],
      },
      "breakdown": dict[str, dict[str, dict[str, int]]],  # breakdown[login][layer][sub_metric] = int
  }
  ```
  For repos with `error != None`, `activity` is not added (matches existing pattern where `_chart_data` returns `{"error": ...}` only). Task 2 will consume this key.

- [ ] **Step 1: Add test for layer sums, sort order, totals invariant**

Open [tests/test_report.py](tests/test_report.py). Append at the end of the file:

```python
# ---------- activity block ----------


def test_activity_layer_sums_and_sort_order() -> None:
    # alice total 22, bob total 12, carol total 12 (bob before carol alphabetically).
    metrics = _metrics({
        "acme/api": _repo(
            commits_by_user={
                "alice": {
                    "commits": 5,
                    "pull_requests_opened": 2, "pull_requests_merged": 1,
                    "issues_opened": 99,
                    "APPROVED": 3, "CHANGES_REQUESTED": 1, "COMMENTED": 1,
                    "review_comments": 2, "pr_conversation_comments": 3, "issue_comments": 4,
                    "cross_team_reviews": 99,
                },
                "bob":   {"commits": 2, "review_comments": 10},
                "carol": {"commits": 6, "pull_requests_opened": 6},
            },
            ts=_ts(),
        ),
    })
    html = render(metrics)
    activity = _find_payload(html)["repos"]["acme/api"]["activity"]

    assert activity["users"]  == ["alice", "bob", "carol"]
    assert activity["totals"] == [22, 12, 12]
    assert activity["layers"]["commits"]  == [5, 2, 6]
    assert activity["layers"]["pr"]       == [8, 0, 6]
    assert activity["layers"]["comments"] == [9, 10, 0]

    # totals[i] invariant.
    for i, u in enumerate(activity["users"]):
        s = (activity["layers"]["commits"][i]
             + activity["layers"]["pr"][i]
             + activity["layers"]["comments"][i])
        assert s == activity["totals"][i], f"totals[{i}] mismatch for {u}"
```

- [ ] **Step 2: Run test — expect failure**

```bash
python3 -m pytest tests/test_report.py::test_activity_layer_sums_and_sort_order -v
```

Expected: FAIL with `KeyError: 'activity'`.

- [ ] **Step 3: Implement `activity` block in `_chart_data`**

Open [gh_contributions/report.py](gh_contributions/report.py). Locate `_chart_data`. Immediately after the line that assigns `result["per_user_raw"] = per_user` and before the `if "team_share" in layers ...` block, insert:

```python
    # Unified per-user activity block: pre-sorted users + three layer sums +
    # per-user sub-metric breakdown for tooltips. Always emitted (even when
    # config layers are disabled — missing sub-metrics contribute 0).
    def _breakdown(u: str) -> dict[str, dict[str, int]]:
        a = per_user.get(u, {}).get("authoring", {}) or {}
        c = per_user.get(u, {}).get("collaboration", {}) or {}
        rg = c.get("reviews_given", {}) or {}
        return {
            "commits":  {"commits": a.get("commits", 0)},
            "pr": {
                "pull_requests_opened": a.get("pull_requests_opened", 0),
                "pull_requests_merged": a.get("pull_requests_merged", 0),
                "APPROVED":             rg.get("APPROVED", 0),
                "CHANGES_REQUESTED":    rg.get("CHANGES_REQUESTED", 0),
                "COMMENTED":            rg.get("COMMENTED", 0),
            },
            "comments": {
                "review_comments":          c.get("review_comments", 0),
                "pr_conversation_comments": c.get("pr_conversation_comments", 0),
                "issue_comments":           c.get("issue_comments", 0),
            },
        }

    breakdown = {u: _breakdown(u) for u in per_user}
    totals_by_user = {
        u: sum(v for layer in b.values() for v in layer.values())
        for u, b in breakdown.items()
    }
    users_sorted = sorted(per_user, key=lambda u: (-totals_by_user[u], u))
    result["activity"] = {
        "users":  users_sorted,
        "totals": [totals_by_user[u] for u in users_sorted],
        "layers": {
            "commits":  [breakdown[u]["commits"]["commits"]                                for u in users_sorted],
            "pr":       [sum(breakdown[u]["pr"].values())                                  for u in users_sorted],
            "comments": [sum(breakdown[u]["comments"].values())                            for u in users_sorted],
        },
        "breakdown": breakdown,
    }
```

- [ ] **Step 4: Run test — expect pass**

```bash
python3 -m pytest tests/test_report.py::test_activity_layer_sums_and_sort_order -v
```

Expected: PASS.

- [ ] **Step 5: Add test for breakdown structure**

Append at the end of [tests/test_report.py](tests/test_report.py):

```python
def test_activity_breakdown_contains_expected_sub_keys() -> None:
    metrics = _metrics({
        "acme/api": _repo(
            commits_by_user={
                "alice": {
                    "commits": 5,
                    "pull_requests_opened": 2, "pull_requests_merged": 1,
                    "APPROVED": 3, "CHANGES_REQUESTED": 1, "COMMENTED": 1,
                    "review_comments": 2, "pr_conversation_comments": 3, "issue_comments": 4,
                },
            },
            ts=_ts(),
        ),
    })
    bd = _find_payload(render(metrics))["repos"]["acme/api"]["activity"]["breakdown"]["alice"]

    assert set(bd) == {"commits", "pr", "comments"}
    assert bd["commits"] == {"commits": 5}
    assert bd["pr"] == {
        "pull_requests_opened": 2,
        "pull_requests_merged": 1,
        "APPROVED": 3,
        "CHANGES_REQUESTED": 1,
        "COMMENTED": 1,
    }
    assert bd["comments"] == {
        "review_comments": 2,
        "pr_conversation_comments": 3,
        "issue_comments": 4,
    }
```

- [ ] **Step 6: Run test — expect pass**

```bash
python3 -m pytest tests/test_report.py::test_activity_breakdown_contains_expected_sub_keys -v
```

Expected: PASS.

- [ ] **Step 7: Add test that excluded metrics don't leak into activity but stay in per_user_raw**

Append at the end of [tests/test_report.py](tests/test_report.py):

```python
def test_activity_excludes_issues_opened_and_cross_team_reviews() -> None:
    metrics = _metrics({
        "acme/api": _repo(
            commits_by_user={
                "alice": {"commits": 1, "issues_opened": 42, "cross_team_reviews": 42},
            },
            ts=_ts(),
        ),
    })
    repo = _find_payload(render(metrics))["repos"]["acme/api"]
    activity = repo["activity"]

    # Excluded metrics must not appear anywhere inside activity.
    activity_str = json.dumps(activity)
    assert "issues_opened" not in activity_str
    assert "cross_team_reviews" not in activity_str
    # Alice's total is just her commit (1); nothing else contributes.
    assert activity["totals"] == [1]

    # But per_user_raw (which feeds the details table) still has them.
    assert repo["per_user_raw"]["alice"]["authoring"]["issues_opened"] == 42
    assert repo["per_user_raw"]["alice"]["collaboration"]["cross_team_reviews"] == 42
```

- [ ] **Step 8: Run test — expect pass**

```bash
python3 -m pytest tests/test_report.py::test_activity_excludes_issues_opened_and_cross_team_reviews -v
```

Expected: PASS.

- [ ] **Step 9: Add test for layer-disabled config (no `collaboration` block on per_user)**

Append at the end of [tests/test_report.py](tests/test_report.py):

```python
def test_activity_handles_missing_collaboration_block() -> None:
    # Simulate authoring-only run: per_user has only the "authoring" key,
    # matching what metrics.py produces when 'collaboration' is not in config.
    metrics = _metrics(
        {
            "acme/api": {
                "per_user": {
                    "alice": {
                        "authoring": {
                            "commits": 3,
                            "pull_requests_opened": 2,
                            "pull_requests_merged": 1,
                            "issues_opened": 0,
                        },
                    },
                },
                "team_share": _ts(),
                "truncated": {},
                "error": None,
            },
        },
        layers=("authoring", "team_share"),
    )
    activity = _find_payload(render(metrics))["repos"]["acme/api"]["activity"]

    assert activity["users"] == ["alice"]
    assert activity["layers"]["commits"]  == [3]
    assert activity["layers"]["pr"]       == [3]  # 2 opened + 1 merged, reviews all 0
    assert activity["layers"]["comments"] == [0]
    assert activity["totals"] == [6]
    # Breakdown still emits all layer sub-keys with 0 fallbacks.
    bd = activity["breakdown"]["alice"]
    assert bd["pr"]["APPROVED"] == 0
    assert bd["comments"]["review_comments"] == 0
```

- [ ] **Step 10: Run test — expect pass**

```bash
python3 -m pytest tests/test_report.py::test_activity_handles_missing_collaboration_block -v
```

Expected: PASS.

- [ ] **Step 11: Add test for empty repo**

Append at the end of [tests/test_report.py](tests/test_report.py):

```python
def test_activity_empty_repo() -> None:
    metrics = _metrics({
        "acme/api": _repo(ts=_ts()),  # commits_by_user=None -> per_user={}
    })
    activity = _find_payload(render(metrics))["repos"]["acme/api"]["activity"]

    assert activity == {
        "users": [],
        "totals": [],
        "layers": {"commits": [], "pr": [], "comments": []},
        "breakdown": {},
    }
```

- [ ] **Step 12: Run test — expect pass**

```bash
python3 -m pytest tests/test_report.py::test_activity_empty_repo -v
```

Expected: PASS.

- [ ] **Step 13: Add test for errored repo omits activity**

Append at the end of [tests/test_report.py](tests/test_report.py):

```python
def test_activity_omitted_for_errored_repo() -> None:
    metrics = _metrics({
        "broken": _repo(error="not_found"),
    })
    repo = _find_payload(render(metrics))["repos"]["broken"]
    assert repo == {"error": "not_found"}  # no activity, no per_user_raw, no team_share
```

- [ ] **Step 14: Run test — expect pass**

```bash
python3 -m pytest tests/test_report.py::test_activity_omitted_for_errored_repo -v
```

Expected: PASS.

- [ ] **Step 15: Add test that activity works uniformly on the aggregate tab**

Append at the end of [tests/test_report.py](tests/test_report.py):

```python
def test_activity_works_on_aggregate_tab() -> None:
    metrics = _metrics({
        "acme/api": _repo(
            commits_by_user={"alice": {"commits": 4}, "bob": {"commits": 1}},
            ts=_ts(),
        ),
        "acme/web": _repo(
            commits_by_user={"alice": {"commits": 2}, "bob": {"commits": 3}},
            ts=_ts(),
        ),
    })
    agg_metrics = {
        "run": metrics["run"],
        "repos": {"__aggregate__": _aggregate(metrics), **metrics["repos"]},
    }
    activity = _find_payload(render(agg_metrics))["repos"]["__aggregate__"]["activity"]

    # alice 6 commits, bob 4 commits -> alice first.
    assert activity["users"] == ["alice", "bob"]
    assert activity["layers"]["commits"] == [6, 4]
    assert activity["totals"] == [6, 4]
```

- [ ] **Step 16: Run test — expect pass**

```bash
python3 -m pytest tests/test_report.py::test_activity_works_on_aggregate_tab -v
```

Expected: PASS.

- [ ] **Step 17: Run the whole suite — expect all pass**

```bash
python3 -m pytest tests/ -q
```

Expected: all tests pass (existing tests untouched; 7 new tests added).

- [ ] **Step 18: Commit**

```bash
git add gh_contributions/report.py tests/test_report.py
git commit -m "feat(report): emit unified activity payload block per repo"
```

---

### Task 2: Render unified chart, update layout, drop old cells and payload keys

**Files:**
- Modify: `gh_contributions/report.py` (constants, `_chart_data`, `_tab_body`, `_cell`, `_CSS`, `_APP_JS`)
- Modify: `tests/test_report.py` (update three existing tests whose assertions reference removed structures)

**Interfaces:**
- Consumes: `activity` block on each per-repo dict (produced by Task 1); existing `payload.run.metrics_layers` list for the disabled-layer note.
- Produces: HTML changes only — no external API. Repo tab body now contains two chart cells (Team share, Activity) plus the details table. `data-chart="activity"` is the sole per-user canvas; `data-chart="authoring"`, `data-chart="reviews"`, `data-chart="comments"` are removed. `activity`, `authoring`, `reviews`, `comments` payload keys are consolidated to just `activity`.

- [ ] **Step 1: Update `test_render_errored_repo_shows_error_banner_in_its_tab`**

Open [tests/test_report.py](tests/test_report.py). Find `test_render_errored_repo_shows_error_banner_in_its_tab`. Change the final assertion from:

```python
    assert healthy_section.count("<canvas") == 4
```

to:

```python
    assert healthy_section.count("<canvas") == 2  # team_share + activity
```

- [ ] **Step 2: Update `test_render_layer_disabled_placeholder`**

Open [tests/test_report.py](tests/test_report.py). Replace the body of `test_render_layer_disabled_placeholder` with:

```python
def test_render_layer_disabled_placeholder() -> None:
    # Only 'authoring' enabled -> team_share cell is a placeholder,
    # activity cell always renders (missing collaboration counts as 0).
    metrics = _metrics(
        {"acme/api": _repo(commits_by_user={"alice": {"commits": 1}}, ts=_ts())},
        layers=("authoring",),
    )
    html = render(metrics)
    section = _extract_section(html, "acme/api")
    assert 'data-chart="activity"' in section
    assert section.count("layer-disabled") == 1  # team_share only
```

- [ ] **Step 3: Replace `test_render_embeds_report_data_payload`**

Open [tests/test_report.py](tests/test_report.py). Replace the body of `test_render_embeds_report_data_payload` (currently references `repo["authoring"]` and `repo["reviews"]`) with a version that verifies only `team_share` and `activity` remain:

```python
def test_render_embeds_report_data_payload() -> None:
    metrics = _metrics({
        "acme/api": _repo(
            commits_by_user={
                "alice": {"commits": 5, "pull_requests_opened": 2, "COMMENTED": 3, "review_comments": 4},
                "bob":   {"commits": 2, "APPROVED": 1, "issue_comments": 1},
            },
            ts=_ts(commits=(7, 10), prs=(2, 5), reviews=(4, 4), comments=(5, 8)),
        ),
    })
    html = render(metrics)
    payload = _find_payload(html)

    assert list(payload["repos"]) == ["acme/api"]
    repo = payload["repos"]["acme/api"]
    assert repo["error"] is None

    # team_share block: parallel arrays over the four buckets.
    assert repo["team_share"]["buckets"] == list(_TEAM_SHARE_BUCKETS_EXPECTED)
    assert repo["team_share"]["team"]   == [7, 2, 4, 5]
    assert repo["team_share"]["total"]  == [10, 5, 4, 8]

    # activity block: users sorted by total desc.
    # alice: commits 5 + PR 2 + reviews 3 = 10; comments 4 -> total 14.
    # bob:   commits 2 + PR 0 + reviews 1 =  3; comments 1 -> total  4.
    assert repo["activity"]["users"] == ["alice", "bob"]
    assert repo["activity"]["totals"] == [14, 4]
    assert repo["activity"]["layers"]["commits"] == [5, 2]

    # Removed payload keys must not appear.
    assert "authoring" not in repo
    assert "reviews" not in repo
    assert "comments" not in repo
```

- [ ] **Step 4: Run the updated tests — expect two failures (Steps 1 & 3), one pass depends on Task 1 state**

```bash
python3 -m pytest tests/test_report.py::test_render_errored_repo_shows_error_banner_in_its_tab tests/test_report.py::test_render_layer_disabled_placeholder tests/test_report.py::test_render_embeds_report_data_payload -v
```

Expected: `test_render_errored_repo_shows_error_banner_in_its_tab` FAIL (still 4 canvases in HTML). `test_render_layer_disabled_placeholder` FAIL (3 layer-disabled cells, not 1). `test_render_embeds_report_data_payload` FAIL (`authoring` key still present).

- [ ] **Step 5: Drop the module-level constants and old payload keys from `_chart_data`**

Open [gh_contributions/report.py](gh_contributions/report.py).

Delete these module-level constants (no longer referenced by `_chart_data`):

```python
_AUTHORING_KEYS = ("commits", "pull_requests_opened", "pull_requests_merged", "issues_opened")
_COLLAB_INT_KEYS = ("review_comments", "pr_conversation_comments", "issue_comments", "cross_team_reviews")
_REVIEW_STATES = ("APPROVED", "CHANGES_REQUESTED", "COMMENTED")
```

`_AUTHORING_KEYS`, `_COLLAB_INT_KEYS`, `_REVIEW_STATES` are used by `_aggregate` too — verify before deleting.

Run first:

```bash
grep -n '_AUTHORING_KEYS\|_COLLAB_INT_KEYS\|_REVIEW_STATES' gh_contributions/report.py
```

Expected uses: all three appear inside `_aggregate` and (`_AUTHORING_KEYS`, `_REVIEW_STATES`, `_COLLAB_INT_KEYS`) inside the now-obsolete branches of `_chart_data`. **Do not delete the constants** — `_aggregate` still needs them. Skip the deletion; move to the `_chart_data` cleanup below.

In `_chart_data`, delete these blocks (the three obsolete per-user chart blocks):

```python
    if "authoring" in layers and per_user:
        def _commits(u: str) -> int:
            return per_user[u].get("authoring", {}).get("commits", 0)
        users = sorted(per_user, key=lambda u: (-_commits(u), u))
        result["authoring"] = {
            "users":                users,
            "commits":              [per_user[u].get("authoring", {}).get("commits", 0) for u in users],
            "pull_requests_opened": [per_user[u].get("authoring", {}).get("pull_requests_opened", 0) for u in users],
            "pull_requests_merged": [per_user[u].get("authoring", {}).get("pull_requests_merged", 0) for u in users],
            "issues_opened":        [per_user[u].get("authoring", {}).get("issues_opened", 0) for u in users],
        }

    if "collaboration" in layers and per_user:
        def _rev_total(u: str) -> int:
            rg = per_user[u].get("collaboration", {}).get("reviews_given", {})
            return sum(rg.values())
        rusers = sorted(per_user, key=lambda u: (-_rev_total(u), u))
        result["reviews"] = {
            "users":              rusers,
            "APPROVED":           [per_user[u].get("collaboration", {}).get("reviews_given", {}).get("APPROVED", 0)          for u in rusers],
            "CHANGES_REQUESTED":  [per_user[u].get("collaboration", {}).get("reviews_given", {}).get("CHANGES_REQUESTED", 0) for u in rusers],
            "COMMENTED":          [per_user[u].get("collaboration", {}).get("reviews_given", {}).get("COMMENTED", 0)         for u in rusers],
        }

        def _com_total(u: str) -> int:
            c = per_user[u].get("collaboration", {})
            return c.get("review_comments", 0) + c.get("pr_conversation_comments", 0) + c.get("issue_comments", 0)
        cusers = sorted(per_user, key=lambda u: (-_com_total(u), u))
        result["comments"] = {
            "users":                    cusers,
            "review_comments":          [per_user[u].get("collaboration", {}).get("review_comments", 0)          for u in cusers],
            "pr_conversation_comments": [per_user[u].get("collaboration", {}).get("pr_conversation_comments", 0) for u in cusers],
            "issue_comments":           [per_user[u].get("collaboration", {}).get("issue_comments", 0)           for u in cusers],
        }
```

Leave the `team_share` block, the new `activity` block, and `per_user_raw` in place.

- [ ] **Step 6: Update `_tab_body` to have only Team share and Activity cells**

In [gh_contributions/report.py](gh_contributions/report.py), replace the `_tab_body` function body (the section that builds `cells`) so it produces only two cells. Replace:

```python
    cells = [
        _cell("team_share", "Team share",    "team_share",    name, layers),
        _cell("authoring",  "Authoring",     "authoring",     name, layers),
        _cell("reviews",    "Reviews given", "collaboration", name, layers),
        _cell("comments",   "Comments",      "collaboration", name, layers),
    ]
    return (
        f'<section data-repo="{name}"{hidden}>'
        f'  <div class="grid">{"".join(cells)}</div>'
        f'  <table class="details" data-repo="{name}"></table>'
        f'</section>'
    )
```

with:

```python
    cells = [
        _cell("team_share", "Team share", "team_share", name, layers),
        _cell("activity",   "Activity",   None,         name, layers),
    ]
    return (
        f'<section data-repo="{name}"{hidden}>'
        f'  <div class="stack">{"".join(cells)}</div>'
        f'  <table class="details" data-repo="{name}"></table>'
        f'</section>'
    )
```

- [ ] **Step 7: Extend `_cell` to accept `required_layer=None`**

In [gh_contributions/report.py](gh_contributions/report.py), replace `_cell` with:

```python
def _cell(chart_key: str, title: str, required_layer: str | None, repo_name: str, layers: set) -> str:
    if required_layer is not None and required_layer not in layers:
        return (
            '<div class="cell layer-disabled">'
            f'<strong>{_esc(title)}</strong>'
            f'<p>Layer <code>{_esc(required_layer)}</code> disabled in config.</p>'
            "</div>"
        )
    extra_class = " cell-team-share" if chart_key == "team_share" else " cell-activity" if chart_key == "activity" else ""
    return (
        f'<div class="cell{extra_class}">'
        f'<canvas data-chart="{chart_key}" data-repo="{repo_name}"></canvas>'
        "</div>"
    )
```

The extra class hooks let CSS constrain Team share width and let the Activity cell hold the layer-note element (Step 9 inserts it via JS).

- [ ] **Step 8: Update `_CSS` — swap grid for a vertical stack; constrain Team share**

In [gh_contributions/report.py](gh_contributions/report.py), replace the `.grid` and `.cell canvas` rules inside `_CSS`. Change:

```css
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.cell { border: 1px solid #eee; padding: 12px; min-height: 280px; }
.cell canvas { max-height: 320px; }
```

to:

```css
.stack { display: flex; flex-direction: column; gap: 16px; }
.cell { border: 1px solid #eee; padding: 12px; }
.cell-team-share { max-width: 480px; }
.cell-team-share canvas { max-height: 320px; }
.layer-note { color: #666; font-size: 12px; margin: 0 0 8px; }
```

- [ ] **Step 9: Replace the three per-user branches in `_APP_JS` with a single `activity` branch and add the layer-note injection**

In [gh_contributions/report.py](gh_contributions/report.py), inside `_APP_JS`, delete these three branches:

```javascript
    if (kind === 'authoring' && repo.authoring) { ... }
    if (kind === 'reviews' && repo.reviews) { ... }
    if (kind === 'comments' && repo.comments) { ... }
```

Replace them with a single new `activity` branch (insert immediately after the `team_share` branch, still inside the `forEach(function(canvas){ ... })`):

```javascript
    if (kind === 'activity' && repo.activity) {
      const act = repo.activity;

      // Inject the disabled-layer note above the canvas if any config
      // metrics layer is off. Runs once per canvas.
      const activeLayers = (data.run && data.run.metrics_layers) || [];
      const disabled = ['authoring', 'collaboration'].filter(function(l){
        return activeLayers.indexOf(l) === -1;
      });
      if (disabled.length && canvas.parentNode) {
        const note = document.createElement('p');
        note.className = 'layer-note';
        note.textContent =
          'Note: ' + disabled.map(function(l){ return '`' + l + '`'; }).join(' and ') +
          ' metrics layer' + (disabled.length > 1 ? 's' : '') +
          ' disabled in config \u2014 affected sub-metrics count as 0.';
        canvas.parentNode.insertBefore(note, canvas);
      }

      // Grow canvas height with user count so horizontal bars stay legible.
      const height = Math.max(200, act.users.length * 28 + 60);
      canvas.style.height = height + 'px';

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

      function tooltipLabel(ctx) {
        const layerKey = ctx.dataset.layerKey;
        const login = ctx.label;
        const layerTotal = ctx.parsed.x;
        const bd = (act.breakdown[login] || {})[layerKey] || {};
        const subKeys = Object.keys(bd);
        // Omit parenthetical when total is 0 or the layer has a single sub-metric.
        if (layerTotal === 0 || subKeys.length <= 1) {
          return layerLabels[layerKey] + ': ' + layerTotal;
        }
        const parts = subKeys
          .filter(function(k){ return bd[k] > 0; })
          .map(function(k){ return (displayNames[k] || k) + ' ' + bd[k]; });
        return layerLabels[layerKey] + ': ' + layerTotal +
               (parts.length ? ' (' + parts.join(', ') + ')' : '');
      }

      new Chart(canvas, {
        type: 'bar',
        data: {
          labels: act.users,
          datasets: [
            { label: layerLabels.commits,  layerKey: 'commits',  data: act.layers.commits,  backgroundColor: color(0) },
            { label: layerLabels.pr,       layerKey: 'pr',       data: act.layers.pr,       backgroundColor: color(1) },
            { label: layerLabels.comments, layerKey: 'comments', data: act.layers.comments, backgroundColor: color(2) },
          ],
        },
        options: {
          indexAxis: 'y',
          maintainAspectRatio: false,
          scales: {
            x: { stacked: true, beginAtZero: true },
            y: { stacked: true },
          },
          plugins: {
            tooltip: {
              callbacks: {
                title: function(ctxs){ return ctxs.length ? ctxs[0].label : ''; },
                label: tooltipLabel,
              },
            },
          },
        },
      });
    }
```

- [ ] **Step 10: Run the three updated existing tests — expect pass**

```bash
python3 -m pytest tests/test_report.py::test_render_errored_repo_shows_error_banner_in_its_tab tests/test_report.py::test_render_layer_disabled_placeholder tests/test_report.py::test_render_embeds_report_data_payload -v
```

Expected: all three PASS.

- [ ] **Step 11: Run the whole suite — expect all pass**

```bash
python3 -m pytest tests/ -q
```

Expected: all tests pass. If any Task 1 test fails now because of the payload-key removal, re-read it — the Task 1 tests only reference `activity`, so they should still pass.

- [ ] **Step 12: Manual smoke check — regenerate the report and inspect**

```bash
# Requires an existing out/<run>/metrics.json; if none exists, this step is skipped.
if ls out/*/metrics.json >/dev/null 2>&1; then
  python3 -m gh_contributions.report
  ls -lh out/*/report.html | tail -1
else
  echo "No existing out/*/metrics.json — skip manual smoke, use pytest coverage only."
fi
```

If a `report.html` was generated: open it in a browser and confirm the per-repo tab shows Team share on top (compact width), a horizontal stacked bar chart below with users sorted by total activity descending, tooltips showing per-layer breakdowns, and — if `collaboration` is off — a small grey note above the chart.

- [ ] **Step 13: Commit**

```bash
git add gh_contributions/report.py tests/test_report.py
git commit -m "feat(report): unify per-user charts into stacked activity view"
```

---

## Self-Review

**Spec coverage:**

- Layer definitions (spec §Layer definitions): Task 1 Step 3 (`_breakdown` + layer sum expressions).
- Excluded metrics (spec §Layer definitions): Task 1 Step 7 test.
- Sort order (spec §Sort order): Task 1 Step 3 (`users_sorted` key) + Step 1 test.
- Disabled-layer behavior (spec §Behavior when a config metrics layer is disabled): Task 1 Step 9 test (zero fill), Task 2 Step 9 (JS note injection).
- Data shape (spec §Data shape): Task 1 Steps 1, 5, 7, 11, 13, 15.
- Layout (spec §Rendering §Layout): Task 2 Steps 6, 8.
- Cell collapse (spec §Rendering §_tab_body cells): Task 2 Steps 6, 7.
- Chart config (spec §Rendering §JS chart config): Task 2 Step 9.
- Tooltip format (spec §Rendering §JS chart config): Task 2 Step 9 (`tooltipLabel` — includes the "single sub-metric or zero total → no parenthetical" rule).
- Disabled-layer note (spec §Rendering §Disabled-layer note): Task 2 Step 9.
- Tests coverage (spec §Testing items 1–8): Task 1 Steps 1, 5, 7, 9, 11, 13, 15 cover items 1–8 respectively (item 3 totals-invariant is embedded in Step 1; item 5 covers excluded metrics + details table intact).
- Existing tests to update (spec §Testing): Task 2 Steps 1, 2, 3.
- Manual verification (spec §Testing): Task 2 Step 12.

No gaps.

**Placeholder scan:** No TBDs, TODOs, or "fill in" markers.

**Type consistency:** All layer keys (`commits`, `pr`, `comments`), the top-level `activity` key, `data-chart="activity"`, and the `layerKey` dataset attribute are used identically across Task 1 and Task 2.

---

## Execution Handoff

Plan complete and saved to [docs/superpowers/plans/2026-07-02-unified-per-user-activity-chart.md](docs/superpowers/plans/2026-07-02-unified-per-user-activity-chart.md). Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
