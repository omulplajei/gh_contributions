"""Render a self-contained HTML report from metrics.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path


_AUTHORING_KEYS = ("commits", "pull_requests_opened", "pull_requests_merged", "issues_opened")
_COLLAB_INT_KEYS = ("review_comments", "pr_conversation_comments", "issue_comments", "cross_team_reviews")
_REVIEW_STATES = ("APPROVED", "CHANGES_REQUESTED", "COMMENTED")
_TEAM_SHARE_SUB_METRICS = {
    "commits":  ("commits",),
    "pr":       ("pull_requests_opened", "pull_requests_merged",
                 "APPROVED", "CHANGES_REQUESTED", "COMMENTED"),
    "comments": ("review_comments", "pr_conversation_comments", "issue_comments"),
}
_LAYER_TITLE = {
    "commits":  "Commits",
    "pr":       "PR activity",
    "comments": "Comments",
}

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
        for layer, sub_keys in _TEAM_SHARE_SUB_METRICS.items():
            team  = {k: sum(ts[layer]["team"].get(k, 0)  for ts in ts_repos) for k in sub_keys}
            total = {k: sum(ts[layer]["total"].get(k, 0) for ts in ts_repos) for k in sub_keys}
            t = sum(team.values())
            n = sum(total.values())
            team_share[layer] = {"team": team, "total": total, "share": (t / n) if n else None}

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
            "commits":  [breakdown[u]["commits"]["commits"] for u in users_sorted],
            "pr":       [sum(breakdown[u]["pr"].values())   for u in users_sorted],
            "comments": [sum(breakdown[u]["comments"].values()) for u in users_sorted],
        },
        "breakdown": breakdown,
    }

    if "team_share" in layers and repo.get("team_share"):
        ts = repo["team_share"]
        layers_list = list(_TEAM_SHARE_SUB_METRICS)
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


def _banners_html(repos: dict) -> str:
    truncated_pairs: list[str] = []
    for name, repo in repos.items():
        for endpoint, flag in (repo.get("truncated") or {}).items():
            if flag:
                truncated_pairs.append(f"{name}/{endpoint}")
    if not truncated_pairs:
        return ""
    items = ", ".join(_esc(p) for p in sorted(truncated_pairs))
    return (
        '<div class="warn-banner">'
        "Some counts are undercounts \u2014 the following endpoints hit GitHub's 1000-result cap: "
        f"{items}."
        "</div>"
    )


_CSS = """
body { font: 14px system-ui, sans-serif; margin: 0; padding: 16px; }
header h1 { margin: 0 0 8px; font-size: 20px; }
header p { margin: 2px 0; color: #555; }
nav#tabs { display: flex; gap: 4px; border-bottom: 1px solid #ddd; margin-top: 16px; }
.tab { border: 1px solid #ddd; background: #f6f6f6; padding: 6px 12px; cursor: pointer; }
.tab.active { background: white; border-bottom-color: white; }
main { padding-top: 12px; }
.stack { display: flex; flex-direction: column; gap: 16px; }
.cell { border: 1px solid #eee; padding: 12px; }
.team-share-row { display: flex; flex-direction: row; flex-wrap: wrap; gap: 16px; }
.cell-pie { flex: 1 1 240px; max-width: 320px; }
.cell-pie canvas { max-height: 260px; }
.pie-empty { color: #888; text-align: center; padding: 24px 12px; }
.layer-note { color: #666; font-size: 12px; margin: 0 0 8px; }
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

  document.querySelectorAll('canvas[data-chart]').forEach(function(canvas){
    const repo = data.repos[canvas.dataset.repo];
    if (!repo || repo.error) return;
    const kind = canvas.dataset.chart;

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
    argv = argv if argv is not None else sys.argv[1:]

    if argv:
        run_dir = Path(argv[0])
    else:
        out_root = Path("out")
        if not out_root.is_dir():
            print("no run directories found under out/", file=sys.stderr)
            return 2
        candidates = sorted(p for p in out_root.iterdir() if p.is_dir())
        if not candidates:
            print("no run directories found under out/", file=sys.stderr)
            return 2
        run_dir = candidates[-1]

    metrics_path = run_dir / "metrics.json"
    if not metrics_path.is_file():
        print(f"report error: metrics.json not found in {run_dir}", file=sys.stderr)
        return 2

    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"report error: malformed metrics.json: {exc}", file=sys.stderr)
        return 2

    agg = _aggregate(metrics)
    if agg is not None:
        metrics = {
            **metrics,
            "repos": {"__aggregate__": agg, **metrics.get("repos", {})},
        }

    html = render(metrics)
    out_path = run_dir / "report.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
