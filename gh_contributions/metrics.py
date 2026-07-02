"""Pure computation of team-activity metrics from on-disk raw pages."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .fetch import _months_between


def compute(raw_root: Path, config: Config, *, today: date | None = None) -> dict:
    if today is None:
        today = datetime.now(timezone.utc).date()
    months = _months_between(config.since, today)
    result: dict[str, Any] = {
        "run": {
            "since": config.since.isoformat(),
            "until": today.isoformat(),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "metrics_layers": list(config.metrics),
        },
        "repos": {},
    }
    for repo in config.repos:
        result["repos"][repo] = _compute_repo(raw_root, months, repo, config, today)
    return result


def _compute_repo(
    raw_root: Path,
    months: list[str],
    repo: str,
    config: Config,
    today: date,
) -> dict:
    owner, name = repo.split("/", 1)

    statuses: dict[str, tuple[str, dict | str | None]] = {
        m: _month_status(raw_root, m, owner, name) for m in months
    }
    good_months    = [m for m, s in statuses.items() if s[0] == "good"]
    errored_months = [m for m, s in statuses.items() if s[0] == "error"]
    # 'absent' months are silently treated as gaps.

    if months and not good_months and errored_months:
        reasons = sorted({str(statuses[m][1]) for m in errored_months})
        reason = reasons[0] if len(reasons) == 1 else "; ".join(reasons)
        return {
            "per_user": None,
            "team_share": None,
            "truncated": None,
            "error": reason,
        }

    per_user: dict[str, dict] = {u: {} for u in config.usernames}
    truncated: dict[str, bool] = {}
    error: str | None = None
    if errored_months:
        parts = [f"{m} ({statuses[m][1]})" for m in errored_months]
        error = "partial: failed months: " + ", ".join(parts)

    for m in good_months:
        meta = statuses[m][1]
        if isinstance(meta, dict):
            for endpoint, entry in meta.items():
                if isinstance(entry, dict) and entry.get("truncated"):
                    truncated[endpoint] = True

    out: dict[str, Any] = {
        "per_user": per_user,
        "team_share": None,
        "truncated": truncated,
        "error": error,
    }

    if "authoring" in config.metrics:
        _apply_authoring(raw_root, good_months, owner, name, config, per_user)

    if "collaboration" in config.metrics:
        _apply_collaboration(raw_root, good_months, owner, name, config, today, per_user)

    if "team_share" in config.metrics:
        _apply_team_share(raw_root, good_months, owner, name, config, today, out)

    return out


def _month_status(
    raw_root: Path,
    month: str,
    owner: str,
    name: str,
) -> tuple[str, dict | str | None]:
    """('good', meta_dict) | ('error', reason_str) | ('absent', None)."""
    repo_dir = raw_root / month / f"{owner}__{name}"
    meta_path = repo_dir / "_meta.json"
    if not repo_dir.exists() or not meta_path.exists():
        return ("absent", None)
    try:
        meta = json.loads(meta_path.read_text())
    except json.JSONDecodeError:
        return ("error", "malformed")
    if not isinstance(meta, dict):
        return ("error", "malformed")
    if "error" in meta:
        return ("error", str(meta["error"]))
    return ("good", meta)


def _load_endpoint(
    raw_root: Path,
    months: list[str],
    owner: str,
    name: str,
    filename: str,
) -> list[dict]:
    out: list[dict] = []
    for m in months:
        path = raw_root / m / f"{owner}__{name}" / filename
        if not path.exists():
            continue
        data = _read_json(path, default=[])
        if isinstance(data, list):
            out.extend(data)
    return out


def _load_reviews(
    raw_root: Path,
    months: list[str],
    owner: str,
    name: str,
) -> dict[int, list[dict]]:
    merged: dict[int, list[dict]] = {}
    for m in months:
        reviews_dir = raw_root / m / f"{owner}__{name}" / "reviews"
        if not reviews_dir.is_dir():
            continue
        for review_file in sorted(reviews_dir.glob("*.json")):
            try:
                pr_number = int(review_file.stem)
            except ValueError:
                continue
            data = _read_json(review_file, default=[])
            if isinstance(data, list):
                merged[pr_number] = data
    return merged


def _apply_authoring(
    raw_root: Path,
    months: list[str],
    owner: str,
    name: str,
    config: Config,
    per_user: dict[str, dict],
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
        for item in _load_endpoint(raw_root, months, owner, name, src):
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


def _window_bounds(config: Config, today: date) -> tuple[datetime, datetime]:
    lo = datetime.combine(config.since, time.min, tzinfo=timezone.utc)
    hi = datetime.combine(today, time(23, 59, 59), tzinfo=timezone.utc)
    return lo, hi


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _in_window(ts: str | None, lo: datetime, hi: datetime) -> bool:
    d = _parse_ts(ts)
    return d is not None and lo <= d <= hi


def _apply_collaboration(
    raw_root: Path,
    months: list[str],
    owner: str,
    name: str,
    config: Config,
    today: date,
    per_user: dict[str, dict],
) -> None:
    team = set(config.usernames)
    lo, hi = _window_bounds(config, today)

    collab = {u: {
        "reviews_given": {s: 0 for s in _REVIEW_STATES},
        "review_comments": 0,
        "pr_conversation_comments": 0,
        "issue_comments": 0,
        "cross_team_reviews": 0,
    } for u in team}

    pr_author_by_number: dict[int, str] = {}
    for pr in _load_endpoint(raw_root, months, owner, name, "prs_updated.json"):
        num = pr.get("number")
        user = pr.get("user") or {}
        if isinstance(num, int) and isinstance(user, dict):
            pr_author_by_number[num] = user.get("login") or ""

    known_pr_numbers = set(pr_author_by_number)

    reviews_by_pr = _load_reviews(raw_root, months, owner, name)
    for pr_number, reviews in reviews_by_pr.items():
        pr_author = pr_author_by_number.get(pr_number, "")
        for r in reviews:
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

    for c in _load_endpoint(raw_root, months, owner, name, "review_comments.json"):
        if not _in_window(c.get("created_at"), lo, hi):
            continue
        login = ((c.get("user") or {}).get("login")) or ""
        if login in team:
            collab[login]["review_comments"] += 1

    for c in _load_endpoint(raw_root, months, owner, name, "issue_comments.json"):
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


def _apply_team_share(
    raw_root: Path,
    months: list[str],
    owner: str,
    name: str,
    config: Config,
    today: date,
    out: dict,
) -> None:
    team = set(config.usernames)
    lo, hi = _window_bounds(config, today)

    commits_team = 0
    commits_total = 0
    for c in _load_endpoint(raw_root, months, owner, name, "commits.json"):
        commits_total += 1
        if _author_login(c, "commits.json") in team:
            commits_team += 1

    opened_team, opened_total = 0, 0
    for p in _load_endpoint(raw_root, months, owner, name, "prs_by_created.json"):
        opened_total += 1
        if _author_login(p, "prs_by_created.json") in team:
            opened_team += 1

    merged_team, merged_total = 0, 0
    for p in _load_endpoint(raw_root, months, owner, name, "prs_by_merged.json"):
        merged_total += 1
        if _author_login(p, "prs_by_merged.json") in team:
            merged_team += 1

    rev_team = {s: 0 for s in _REVIEW_STATES}
    rev_total = {s: 0 for s in _REVIEW_STATES}
    for reviews in _load_reviews(raw_root, months, owner, name).values():
        for r in reviews:
            state = r.get("state")
            if state not in _REVIEW_STATES:
                continue
            if not _in_window(r.get("submitted_at"), lo, hi):
                continue
            rev_total[state] += 1
            if ((r.get("user") or {}).get("login")) in team:
                rev_team[state] += 1

    rc_team, rc_total = 0, 0
    for c in _load_endpoint(raw_root, months, owner, name, "review_comments.json"):
        if not _in_window(c.get("created_at"), lo, hi):
            continue
        rc_total += 1
        if ((c.get("user") or {}).get("login")) in team:
            rc_team += 1

    prs_updated = _load_endpoint(raw_root, months, owner, name, "prs_updated.json")
    known_pr_numbers = {
        p.get("number") for p in prs_updated if isinstance(p.get("number"), int)
    }
    prc_team, prc_total = 0, 0
    ic_team, ic_total = 0, 0
    for c in _load_endpoint(raw_root, months, owner, name, "issue_comments.json"):
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
