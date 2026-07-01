"""Render a self-contained HTML report from metrics.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path


_AUTHORING_KEYS = ("commits", "pull_requests_opened", "pull_requests_merged", "issues_opened")
_COLLAB_INT_KEYS = ("review_comments", "pr_conversation_comments", "issue_comments", "cross_team_reviews")
_REVIEW_STATES = ("APPROVED", "CHANGES_REQUESTED", "COMMENTED")
_TEAM_SHARE_BUCKETS = ("commits", "pull_requests_opened", "reviews_given", "comments")

_ASSET_DIR = Path(__file__).parent / "assets"


def _aggregate(metrics: dict) -> dict | None:
    healthy = {r: v for r, v in metrics.get("repos", {}).items() if not v.get("error")}
    if not healthy:
        return None

    per_user: dict[str, dict] = {}
    for repo_v in healthy.values():
        for login, layers in (repo_v.get("per_user") or {}).items():
            slot = per_user.setdefault(login, {})
            if "authoring" in layers:
                auth = slot.setdefault("authoring", {k: 0 for k in _AUTHORING_KEYS})
                for k in _AUTHORING_KEYS:
                    auth[k] += layers["authoring"].get(k, 0)
            if "collaboration" in layers:
                collab = slot.setdefault("collaboration", {
                    "reviews_given": {s: 0 for s in _REVIEW_STATES},
                    **{k: 0 for k in _COLLAB_INT_KEYS},
                })
                for s in _REVIEW_STATES:
                    collab["reviews_given"][s] += layers["collaboration"]["reviews_given"].get(s, 0)
                for k in _COLLAB_INT_KEYS:
                    collab[k] += layers["collaboration"].get(k, 0)

    team_share = None
    ts_repos = [v.get("team_share") for v in healthy.values() if v.get("team_share")]
    if ts_repos:
        team_share = {}
        for bucket in _TEAM_SHARE_BUCKETS:
            t = sum(ts[bucket]["team"] for ts in ts_repos)
            n = sum(ts[bucket]["total"] for ts in ts_repos)
            team_share[bucket] = {"team": t, "total": n, "share": (t / n) if n else None}

    truncated: dict[str, bool] = {}
    for v in healthy.values():
        for k, flag in (v.get("truncated") or {}).items():
            if flag:
                truncated[k] = True

    return {"per_user": per_user, "team_share": team_share, "truncated": truncated, "error": None}


def render(metrics: dict) -> str:
    repos = metrics.get("repos", {})
    run = metrics.get("run", {})
    layers = set(run.get("metrics_layers", []))

    payload = {
        "run": run,
        "repos": {name: _chart_data(repo, layers) for name, repo in repos.items()},
    }

    if not repos:
        body = '<main><p class="empty">No repos in this run.</p></main>'
        tabs = ""
    else:
        tabs = "\n".join(_tab_button(name, i == 0) for i, name in enumerate(repos))
        bodies = "\n".join(_tab_body(name, repo, layers, i == 0) for i, (name, repo) in enumerate(repos.items()))
        body = f'<nav id="tabs">{tabs}</nav>\n<main id="tab-bodies">{bodies}</main>'

    banners = _banners_html(repos)
    return _wrap(payload, banners, body, run)


def _chart_data(repo: dict, layers: set) -> dict:
    if repo.get("error"):
        return {"error": repo["error"]}

    result: dict = {"error": None}
    per_user = repo.get("per_user") or {}

    # Raw per-user payload for the details table (browser-side rendered).
    result["per_user_raw"] = per_user

    if "team_share" in layers and repo.get("team_share"):
        ts = repo["team_share"]
        buckets = list(_TEAM_SHARE_BUCKETS)
        result["team_share"] = {
            "buckets": buckets,
            "team":    [ts[b]["team"]  for b in buckets],
            "total":   [ts[b]["total"] for b in buckets],
            "share":   [ts[b]["share"] for b in buckets],
        }

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

    return result


def _tab_button(name: str, active: bool) -> str:
    label = "All repos" if name == "__aggregate__" else name
    cls = "tab active" if active else "tab"
    return f'<button class="{cls}" data-repo="{name}">{_esc(label)}</button>'


def _tab_body(name: str, repo: dict, layers: set, active: bool) -> str:
    label = "All repos" if name == "__aggregate__" else name
    hidden = "" if active else " hidden"
    if repo.get("error"):
        return (
            f'<section data-repo="{name}"{hidden}>'
            f'  <div class="error-banner">{_esc(label)}: {_esc(repo["error"])}</div>'
            f'</section>'
        )
    return (
        f'<section data-repo="{name}"{hidden}>'
        f'  <div class="grid">'
        f'    <div class="cell"><canvas data-chart="team_share" data-repo="{name}"></canvas></div>'
        f'    <div class="cell"><canvas data-chart="authoring"  data-repo="{name}"></canvas></div>'
        f'    <div class="cell"><canvas data-chart="reviews"    data-repo="{name}"></canvas></div>'
        f'    <div class="cell"><canvas data-chart="comments"   data-repo="{name}"></canvas></div>'
        f'  </div>'
        f'  <table class="details" data-repo="{name}"></table>'
        f'</section>'
    )


def _banners_html(repos: dict) -> str:
    return ""  # Task 5 fills in truncation + error banners.


_CSS = """
body { font: 14px system-ui, sans-serif; margin: 0; padding: 16px; }
header h1 { margin: 0 0 8px; font-size: 20px; }
header p { margin: 2px 0; color: #555; }
nav#tabs { display: flex; gap: 4px; border-bottom: 1px solid #ddd; margin-top: 16px; }
.tab { border: 1px solid #ddd; background: #f6f6f6; padding: 6px 12px; cursor: pointer; }
.tab.active { background: white; border-bottom-color: white; }
main { padding-top: 12px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.cell { border: 1px solid #eee; padding: 12px; min-height: 280px; }
.cell canvas { max-height: 320px; }
table.details { border-collapse: collapse; margin-top: 16px; width: 100%; }
table.details th, table.details td { border: 1px solid #eee; padding: 4px 8px; text-align: right; }
table.details th { background: #fafafa; cursor: pointer; }
table.details th:first-child, table.details td:first-child { text-align: left; }
.empty { color: #888; padding: 40px; text-align: center; }
.error-banner { background: #ffe6e6; border: 1px solid #f5a3a3; padding: 12px; }
.warn-banner  { background: #fff8dc; border: 1px solid #e0c060; padding: 12px; margin-bottom: 12px; }
"""

_APP_JS = r"""
(function(){
  const raw = document.getElementById('report-data').textContent;
  const data = JSON.parse(raw);

  document.querySelectorAll('nav#tabs .tab').forEach(function(btn){
    btn.addEventListener('click', function(){
      const target = btn.dataset.repo;
      document.querySelectorAll('nav#tabs .tab').forEach(function(b){ b.classList.remove('active'); });
      btn.classList.add('active');
      document.querySelectorAll('main#tab-bodies > section').forEach(function(s){
        s.hidden = (s.dataset.repo !== target);
      });
    });
  });

  const palette = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#14b8a6'];
  function color(i){ return palette[i % palette.length]; }

  document.querySelectorAll('canvas[data-chart]').forEach(function(canvas){
    const repo = data.repos[canvas.dataset.repo];
    if (!repo || repo.error) return;
    const kind = canvas.dataset.chart;

    if (kind === 'team_share' && repo.team_share) {
      const ts = repo.team_share;
      new Chart(canvas, {
        type: 'bar',
        data: {
          labels: ts.buckets,
          datasets: [{
            label: 'Team share',
            data: ts.share.map(function(s){ return s === null ? 0 : s; }),
            backgroundColor: color(0),
          }],
        },
        options: {
          scales: { y: { min: 0, max: 1, ticks: { callback: function(v){ return (v * 100) + '%'; } } } },
          plugins: {
            tooltip: {
              callbacks: {
                label: function(ctx){
                  const i = ctx.dataIndex;
                  const team = ts.team[i], total = ts.total[i];
                  if (total === 0) return 'no data in window';
                  return team + ' / ' + total + '  (' + (ctx.parsed.y * 100).toFixed(1) + '%)';
                }
              }
            }
          }
        }
      });
    }

    if (kind === 'authoring' && repo.authoring) {
      const a = repo.authoring;
      new Chart(canvas, {
        type: 'bar',
        data: {
          labels: a.users,
          datasets: [
            { label: 'commits',              data: a.commits,              backgroundColor: color(0) },
            { label: 'PRs opened',           data: a.pull_requests_opened, backgroundColor: color(1) },
            { label: 'PRs merged',           data: a.pull_requests_merged, backgroundColor: color(2) },
            { label: 'issues opened',        data: a.issues_opened,        backgroundColor: color(3) },
          ],
        },
        options: { scales: { y: { beginAtZero: true } } }
      });
    }

    if (kind === 'reviews' && repo.reviews) {
      const r = repo.reviews;
      new Chart(canvas, {
        type: 'bar',
        data: {
          labels: r.users,
          datasets: [
            { label: 'APPROVED',          data: r.APPROVED,          backgroundColor: color(1) },
            { label: 'CHANGES_REQUESTED', data: r.CHANGES_REQUESTED, backgroundColor: color(3) },
            { label: 'COMMENTED',         data: r.COMMENTED,         backgroundColor: color(0) },
          ],
        },
        options: { scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } } }
      });
    }

    if (kind === 'comments' && repo.comments) {
      const c = repo.comments;
      new Chart(canvas, {
        type: 'bar',
        data: {
          labels: c.users,
          datasets: [
            { label: 'review comments',       data: c.review_comments,          backgroundColor: color(0) },
            { label: 'PR conversation',       data: c.pr_conversation_comments, backgroundColor: color(4) },
            { label: 'issue comments',        data: c.issue_comments,           backgroundColor: color(5) },
          ],
        },
        options: { scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } } }
      });
    }
  });
})();
"""

_TABLE_JS = r"""
(function(){
  const data = JSON.parse(document.getElementById('report-data').textContent);
  const columns = [
    { key: 'login',                    label: 'user',         get: function(u, v){ return u; } },
    { key: 'commits',                  label: 'commits',      get: function(u, v){ return (v.authoring||{}).commits||0; } },
    { key: 'pull_requests_opened',     label: 'PRs opened',   get: function(u, v){ return (v.authoring||{}).pull_requests_opened||0; } },
    { key: 'pull_requests_merged',     label: 'PRs merged',   get: function(u, v){ return (v.authoring||{}).pull_requests_merged||0; } },
    { key: 'issues_opened',            label: 'issues',       get: function(u, v){ return (v.authoring||{}).issues_opened||0; } },
    { key: 'reviews_APPROVED',         label: 'approved',     get: function(u, v){ return ((v.collaboration||{}).reviews_given||{}).APPROVED||0; } },
    { key: 'reviews_CHANGES',          label: 'changes',      get: function(u, v){ return ((v.collaboration||{}).reviews_given||{}).CHANGES_REQUESTED||0; } },
    { key: 'reviews_COMMENTED',        label: 'commented',    get: function(u, v){ return ((v.collaboration||{}).reviews_given||{}).COMMENTED||0; } },
    { key: 'review_comments',          label: 'review cmt',   get: function(u, v){ return (v.collaboration||{}).review_comments||0; } },
    { key: 'pr_conversation_comments', label: 'PR conv cmt',  get: function(u, v){ return (v.collaboration||{}).pr_conversation_comments||0; } },
    { key: 'issue_comments',           label: 'issue cmt',    get: function(u, v){ return (v.collaboration||{}).issue_comments||0; } },
    { key: 'cross_team_reviews',       label: 'cross-team',   get: function(u, v){ return (v.collaboration||{}).cross_team_reviews||0; } },
  ];

  function render(table, users, sortKey, asc){
    const rows = Object.keys(users).map(function(u){
      const row = {};
      columns.forEach(function(c){ row[c.key] = c.get(u, users[u]); });
      return row;
    });
    rows.sort(function(a, b){
      const av = a[sortKey], bv = b[sortKey];
      if (av === bv) return 0;
      const cmp = (av < bv) ? -1 : 1;
      return asc ? cmp : -cmp;
    });
    let html = '<thead><tr>';
    columns.forEach(function(c){
      const arrow = (c.key === sortKey) ? (asc ? ' \u25b2' : ' \u25bc') : '';
      html += '<th data-key="' + c.key + '">' + c.label + arrow + '</th>';
    });
    html += '</tr></thead><tbody>';
    rows.forEach(function(r){
      html += '<tr>';
      columns.forEach(function(c){ html += '<td>' + r[c.key] + '</td>'; });
      html += '</tr>';
    });
    html += '</tbody>';
    table.innerHTML = html;
    table.querySelectorAll('th').forEach(function(th){
      th.addEventListener('click', function(){
        const key = th.dataset.key;
        const nextAsc = (key === sortKey) ? !asc : (key === 'login');
        render(table, users, key, nextAsc);
      });
    });
  }

  document.querySelectorAll('table.details').forEach(function(table){
    const repo = data.repos[table.dataset.repo];
    if (!repo || repo.error || !repo.per_user_raw) return;
    render(table, repo.per_user_raw, 'commits', false);
  });
})();
"""


def _wrap(payload: dict, banners: str, body: str, run: dict) -> str:
    chart_js = (_ASSET_DIR / "chart.umd.min.js").read_text(encoding="utf-8")
    payload_json = json.dumps(payload)
    return (
        "<!doctype html>\n"
        "<html>\n"
        "<head>\n"
        '<meta charset="utf-8">\n'
        f'<title>Contribution Report — {_esc(run.get("since", ""))} to {_esc(run.get("until", ""))}</title>\n'
        f"<style>{_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        "<header>\n"
        "<h1>Contribution Report</h1>\n"
        f'<p>Window: {_esc(run.get("since", ""))} — {_esc(run.get("until", ""))} · Generated {_esc(run.get("generated_at", ""))}</p>\n'
        "</header>\n"
        f"{banners}\n"
        f"{body}\n"
        f'<script id="report-data" type="application/json">{payload_json}</script>\n'
        f"<script>{chart_js}</script>\n"
        f"<script>{_APP_JS}</script>\n"
        f"<script>{_TABLE_JS}</script>\n"
        "</body>\n"
        "</html>\n"
    )


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError("main implemented in Task 6")
