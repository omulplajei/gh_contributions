"""Pure computation of team-activity metrics from on-disk raw pages."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

from .config import Config


def compute(raw_dir: Path, config: Config) -> dict:
    result: dict[str, Any] = {
        "run": {
            "since": config.since.isoformat(),
            "until": config.until.isoformat(),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "metrics_layers": list(config.metrics),
        },
        "repos": {},
    }
    for repo in config.repos:
        result["repos"][repo] = _compute_repo(raw_dir, repo, config)
    return result


def _compute_repo(raw_dir: Path, repo: str, config: Config) -> dict:
    owner, name = repo.split("/", 1)
    repo_dir = raw_dir / f"{owner}__{name}"

    meta = _read_json(repo_dir / "_meta.json", default={})
    if isinstance(meta, dict) and meta.get("error"):
        return {
            "per_user": None,
            "team_share": None,
            "truncated": None,
            "error": meta["error"],
        }

    per_user: dict[str, dict] = {u: {} for u in config.usernames}
    truncated: dict[str, bool] = {}
    out: dict[str, Any] = {
        "per_user": per_user,
        "team_share": None,
        "truncated": truncated,
        "error": None,
    }

    if isinstance(meta, dict):
        for endpoint, entry in meta.items():
            if isinstance(entry, dict) and entry.get("truncated"):
                truncated[endpoint] = True

    if "authoring" in config.metrics:
        _apply_authoring(repo_dir, config, per_user, truncated)

    if "collaboration" in config.metrics:
        _apply_collaboration(repo_dir, config, per_user, truncated)

    if "team_share" in config.metrics:
        _apply_team_share(repo_dir, config, out)

    return out


def _apply_authoring(
    repo_dir: Path,
    config: Config,
    per_user: dict[str, dict],
    truncated: dict[str, bool],
) -> None:
    team = set(config.usernames)
    counts = {u: {
        "commits": 0,
        "pull_requests_opened": 0,
        "pull_requests_merged": 0,
        "issues_opened": 0,
    } for u in team}

    for src, key in [
        ("commits.json",           "commits"),
        ("prs_by_created.json",    "pull_requests_opened"),
        ("prs_by_merged.json",     "pull_requests_merged"),
        ("issues_by_created.json", "issues_opened"),
    ]:
        for item in _read_json(repo_dir / src, default=[]):
            login = _author_login(item, src)
            if login in team:
                counts[login][key] += 1

    for u in team:
        per_user[u]["authoring"] = counts[u]


def _author_login(item: dict, src: str) -> str | None:
    # commits.json uses top-level `author.login`; PR/issue search results use `user.login`.
    if src == "commits.json":
        author = item.get("author") or {}
        return author.get("login") if isinstance(author, dict) else None
    user = item.get("user") or {}
    return user.get("login") if isinstance(user, dict) else None


def _read_json(path: Path, *, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


_REVIEW_STATES = ("APPROVED", "CHANGES_REQUESTED", "COMMENTED")


_TEAM_SHARE_SUB_METRICS = {
    "commits":  ("commits",),
    "pr":       ("pull_requests_opened", "pull_requests_merged",
                 "APPROVED", "CHANGES_REQUESTED", "COMMENTED"),
    "comments": ("review_comments", "pr_conversation_comments", "issue_comments"),
}


def _window_bounds(config: Config) -> tuple[datetime, datetime]:
    lo = datetime.combine(config.since, time.min, tzinfo=timezone.utc)
    hi = datetime.combine(config.until, time(23, 59, 59), tzinfo=timezone.utc)
    return lo, hi


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _in_window(ts: str | None, lo: datetime, hi: datetime) -> bool:
    d = _parse_ts(ts)
    return d is not None and lo <= d <= hi


def _apply_collaboration(
    repo_dir: Path,
    config: Config,
    per_user: dict[str, dict],
    truncated: dict[str, bool],
) -> None:
    team = set(config.usernames)
    lo, hi = _window_bounds(config)

    collab = {u: {
        "reviews_given": {s: 0 for s in _REVIEW_STATES},
        "review_comments": 0,
        "pr_conversation_comments": 0,
        "issue_comments": 0,
        "cross_team_reviews": 0,
    } for u in team}

    # PR author map from prs_updated.json (login by PR number).
    pr_author_by_number: dict[int, str] = {}
    for pr in _read_json(repo_dir / "prs_updated.json", default=[]):
        num = pr.get("number")
        user = pr.get("user") or {}
        if isinstance(num, int) and isinstance(user, dict):
            pr_author_by_number[num] = user.get("login") or ""

    known_pr_numbers = set(pr_author_by_number)

    # Reviews: iterate reviews/<number>.json files.
    reviews_dir = repo_dir / "reviews"
    if reviews_dir.is_dir():
        for review_file in sorted(reviews_dir.glob("*.json")):
            pr_number = int(review_file.stem)
            pr_author = pr_author_by_number.get(pr_number, "")
            for r in _read_json(review_file, default=[]):
                state = r.get("state")
                if state not in _REVIEW_STATES:
                    continue
                if not _in_window(r.get("submitted_at"), lo, hi):
                    continue
                reviewer = ((r.get("user") or {}).get("login")) or ""
                if reviewer not in team:
                    continue
                collab[reviewer]["reviews_given"][state] += 1
                if pr_author and pr_author not in team:
                    collab[reviewer]["cross_team_reviews"] += 1

    # Review comments (inline PR review comments), repo-wide.
    for c in _read_json(repo_dir / "review_comments.json", default=[]):
        if not _in_window(c.get("created_at"), lo, hi):
            continue
        login = ((c.get("user") or {}).get("login")) or ""
        if login in team:
            collab[login]["review_comments"] += 1

    # Issue comments: split into PR-conversation vs issue comments by parent number.
    for c in _read_json(repo_dir / "issue_comments.json", default=[]):
        if not _in_window(c.get("created_at"), lo, hi):
            continue
        login = ((c.get("user") or {}).get("login")) or ""
        if login not in team:
            continue
        parent = _parent_number(c.get("issue_url"))
        if parent is not None and parent in known_pr_numbers:
            collab[login]["pr_conversation_comments"] += 1
        else:
            collab[login]["issue_comments"] += 1

    for u in team:
        per_user[u]["collaboration"] = collab[u]


def _parent_number(issue_url: str | None) -> int | None:
    if not issue_url:
        return None
    tail = issue_url.rstrip("/").rsplit("/", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return None


def _apply_team_share(repo_dir: Path, config: Config, out: dict) -> None:
    team = set(config.usernames)
    lo, hi = _window_bounds(config)

    # commits — Search results are already window-filtered by the query.
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
