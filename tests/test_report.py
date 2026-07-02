"""Tests for gh_contributions.report."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gh_contributions.report import _aggregate, _chart_data, render, main


def _repo(commits_by_user=None, ts=None, truncated=None, error=None):
    """Build a minimal per-repo entry matching metrics.py output shape."""
    per_user = None if error else {}
    if commits_by_user:
        for login, counts in commits_by_user.items():
            per_user[login] = {
                "authoring": {
                    "commits": counts.get("commits", 0),
                    "pull_requests_opened": counts.get("pull_requests_opened", 0),
                    "pull_requests_merged": counts.get("pull_requests_merged", 0),
                    "issues_opened": counts.get("issues_opened", 0),
                },
                "collaboration": {
                    "reviews_given": {
                        "APPROVED":          counts.get("APPROVED", 0),
                        "CHANGES_REQUESTED": counts.get("CHANGES_REQUESTED", 0),
                        "COMMENTED":         counts.get("COMMENTED", 0),
                    },
                    "review_comments":          counts.get("review_comments", 0),
                    "pr_conversation_comments": counts.get("pr_conversation_comments", 0),
                    "issue_comments":           counts.get("issue_comments", 0),
                    "cross_team_reviews":       counts.get("cross_team_reviews", 0),
                },
            }
    return {
        "per_user": per_user,
        "team_share": ts,
        "truncated": None if error else (truncated or {}),
        "error": error,
    }


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


_TEAM_SHARE_SUB_METRICS = {
    "commits":  ("commits",),
    "pr":       ("pull_requests_opened", "pull_requests_merged",
                 "APPROVED", "CHANGES_REQUESTED", "COMMENTED"),
    "comments": ("review_comments", "pr_conversation_comments", "issue_comments"),
}


def _metrics(repos, layers=("authoring", "collaboration", "team_share")):
    return {
        "run": {
            "since": "2026-01-01",
            "until": "2026-06-30",
            "generated_at": "2026-07-01T20:00:00Z",
            "metrics_layers": list(layers),
        },
        "repos": repos,
    }


# ---------- _aggregate ----------


def test_aggregate_sums_per_user_across_repos() -> None:
    metrics = _metrics({
        "acme/api": _repo(
            commits_by_user={
                "alice": {"commits": 5, "pull_requests_opened": 2, "APPROVED": 3, "review_comments": 4},
                "bob":   {"commits": 2, "issues_opened": 1},
            },
            ts=_ts(commits=(7, 10)),
        ),
        "acme/web": _repo(
            commits_by_user={
                "alice": {"commits": 3, "pull_requests_opened": 1, "APPROVED": 1},
            },
            ts=_ts(commits=(3, 5)),
        ),
    })
    agg = _aggregate(metrics)
    assert agg["per_user"]["alice"]["authoring"] == {
        "commits": 8, "pull_requests_opened": 3, "pull_requests_merged": 0, "issues_opened": 0,
    }
    assert agg["per_user"]["bob"]["authoring"] == {
        "commits": 2, "pull_requests_opened": 0, "pull_requests_merged": 0, "issues_opened": 1,
    }
    assert agg["per_user"]["alice"]["collaboration"]["reviews_given"] == {
        "APPROVED": 4, "CHANGES_REQUESTED": 0, "COMMENTED": 0,
    }
    assert agg["per_user"]["alice"]["collaboration"]["review_comments"] == 4


def test_aggregate_recomputes_team_share_ratios() -> None:
    metrics = _metrics({
        "acme/api": _repo(ts=_ts(commits=(7, 10))),
        "acme/web": _repo(ts=_ts(commits=(3, 5))),
    })
    agg = _aggregate(metrics)
    assert agg["team_share"]["commits"]["team"]  == {"commits": 10}
    assert agg["team_share"]["commits"]["total"] == {"commits": 15}
    assert agg["team_share"]["commits"]["share"] == pytest.approx(10 / 15)
    assert "by_month" in agg["team_share"]["commits"]
    # pr and comments were zero on both repos -> summed to all-zero sub-maps and share=None.
    assert agg["team_share"]["pr"]["share"] is None
    assert agg["team_share"]["pr"]["team"]["APPROVED"] == 0
    assert agg["team_share"]["pr"]["total"]["pull_requests_opened"] == 0
    assert agg["team_share"]["comments"]["share"] is None


def test_aggregate_unions_truncation_flags() -> None:
    metrics = _metrics({
        "acme/api": _repo(ts=_ts(), truncated={}),
        "acme/web": _repo(ts=_ts(), truncated={"commits": True}),
    })
    agg = _aggregate(metrics)
    assert agg["truncated"] == {"commits": True}


def test_aggregate_returns_none_if_all_repos_errored() -> None:
    metrics = _metrics({
        "acme/api": _repo(error="not_found"),
    })
    assert _aggregate(metrics) is None


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
    assert agg["team_share"]["commits"]["team"]  == {"commits": 4}
    assert agg["team_share"]["commits"]["total"] == {"commits": 8}
    assert agg["team_share"]["commits"]["share"] == 0.5
    assert "by_month" in agg["team_share"]["commits"]


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
            "team_share": {
                "commits":  {**_ts()["commits"]},
                "pr":       {**pr, "by_month": {}},
                "comments": {**_ts()["comments"]},
            },
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


# ---------- render (happy path) ----------


def _find_payload(html: str) -> dict:
    """Extract and parse the JSON payload embedded in the report."""
    tag = '<script id="report-data" type="application/json">'
    start = html.index(tag) + len(tag)
    end = html.index("</script>", start)
    return json.loads(html[start:end])


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


def test_render_produces_tab_button_per_repo() -> None:
    metrics = _metrics({
        "__aggregate__": _repo(ts=_ts()),
        "acme/api":      _repo(ts=_ts()),
        "orgx/repoy":    _repo(ts=_ts()),
    })
    html = render(metrics)
    # Tabs use data-repo="<name>" on buttons; count each.
    for name in ("__aggregate__", "acme/api", "orgx/repoy"):
        assert f'data-repo="{name}"' in html
    # __aggregate__ button comes before the real repos in document order.
    assert html.index('data-repo="__aggregate__"') < html.index('data-repo="acme/api"')
    assert html.index('data-repo="acme/api"') < html.index('data-repo="orgx/repoy"')


def test_render_empty_repos_shows_no_repos_panel() -> None:
    metrics = _metrics({})
    html = render(metrics)
    assert "No repos in this run" in html
    assert 'data-repo="' not in html  # no tab buttons
    assert '<script id="report-data"' in html  # payload still present but with empty repos
    payload = _find_payload(html)
    assert payload["repos"] == {}


# ---------- render (banners, errors, layer selection) ----------


def test_render_shows_truncation_banner_when_any_endpoint_truncated() -> None:
    metrics = _metrics({
        "acme/api": _repo(ts=_ts(), truncated={"commits": True}),
    })
    html = render(metrics)
    assert 'class="warn-banner"' in html
    assert "acme/api" in html
    assert "commits" in html  # endpoint name surfaced


def test_render_no_truncation_banner_when_nothing_truncated() -> None:
    metrics = _metrics({
        "acme/api": _repo(ts=_ts(), truncated={}),
    })
    html = render(metrics)
    assert 'class="warn-banner"' not in html


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
    assert healthy_section.count("<canvas") == 7  # 3 pies + 3 trend + 1 activity


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

    # Placeholder card text present for the two empty layers (pies + trend cells).
    assert section.count("pie-empty") == 2
    assert section.count("trend-empty") == 2
    assert section.count("no data in window") == 4


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


# ---------- render.trend-row ----------


def test_render_includes_trend_row_with_three_canvases() -> None:
    by_month = {
        "commits": {
            "2026-02": {"team": {"commits": 2}, "total": {"commits": 5}, "share": 0.4},
        },
        "pr": {
            "2026-02": {"team": {"pull_requests_opened": 1}, "total": {"pull_requests_opened": 2}, "share": 0.5},
        },
        "comments": {
            "2026-02": {"team": {"review_comments": 1}, "total": {"review_comments": 3}, "share": 1/3},
        },
    }
    metrics = _metrics({
        "acme/api": _repo(ts=_ts(commits=(2, 5), pr=(1, 2), comments=(1, 3), by_month=by_month)),
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


def _extract_section(html: str, repo: str) -> str:
    marker = f'data-repo="{repo}"'
    start = html.index(f'<section {marker}')
    end = html.index("</section>", start) + len("</section>")
    return html[start:end]


# ---------- main (CLI smoke) ----------


def _valid_metrics_dict() -> dict:
    return _metrics({
        "acme/api": _repo(
            commits_by_user={"alice": {"commits": 3}},
            ts=_ts(commits=(3, 5)),
        ),
    })


def test_main_writes_report_html_and_exits_zero(tmp_path: Path) -> None:
    (tmp_path / "metrics.json").write_text(json.dumps(_valid_metrics_dict()))
    rc = main([str(tmp_path)])
    assert rc == 0
    out = tmp_path / "report.html"
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    assert len(body) > 150000
    assert 'data-repo="__aggregate__"' in body


def test_main_missing_metrics_json_exits_two(tmp_path: Path) -> None:
    rc = main([str(tmp_path)])  # empty dir, no metrics.json
    assert rc == 2


def test_main_picks_newest_run_dir_when_no_arg(tmp_path, monkeypatch) -> None:
    out_root = tmp_path / "out"
    older = out_root / "2026-01-01T000000Z"
    newer = out_root / "2026-02-01T000000Z"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    (older / "metrics.json").write_text(json.dumps(_valid_metrics_dict()))
    (newer / "metrics.json").write_text(json.dumps(_valid_metrics_dict()))
    monkeypatch.chdir(tmp_path)

    rc = main([])
    assert rc == 0
    assert (newer / "report.html").exists()
    assert not (older / "report.html").exists()


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


def test_activity_omitted_for_errored_repo() -> None:
    metrics = _metrics({
        "broken": _repo(error="not_found"),
    })
    repo = _find_payload(render(metrics))["repos"]["broken"]
    assert repo == {"error": "not_found"}  # no activity, no per_user_raw, no team_share


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
