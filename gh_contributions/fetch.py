"""Per-repo fetchers. Write raw JSON pages to disk; no aggregation."""

from __future__ import annotations

import json
import sys
from calendar import monthrange
from datetime import date
from pathlib import Path

from .github_client import (
    GitHubClient,
    NotFoundError,
    RateLimitError,
    SearchPage,
)


def fetch_repo(
    client: GitHubClient,
    repo: str,
    since: date,
    until: date,
    out_dir: Path,
) -> None:
    owner, name = repo.split("/", 1)
    repo_dir = out_dir / f"{owner}__{name}"
    repo_dir.mkdir(parents=True, exist_ok=True)

    date_range = f"{since.isoformat()}..{until.isoformat()}"
    meta: dict[str, dict] = {}

    try:
        # 1. commits (search)
        _write_search(
            repo_dir / "commits.json", meta, "commits",
            client, "/search/commits",
            {"q": f"repo:{repo} committer-date:{date_range}"},
        )
        # 2. PRs opened (search)
        _write_search(
            repo_dir / "prs_by_created.json", meta, "prs_by_created",
            client, "/search/issues",
            {"q": f"repo:{repo} is:pr created:{date_range}"},
        )
        # 3. PRs merged (search)
        _write_search(
            repo_dir / "prs_by_merged.json", meta, "prs_by_merged",
            client, "/search/issues",
            {"q": f"repo:{repo} is:pr merged:{date_range}"},
        )
        # 4. issues opened (search)
        _write_search(
            repo_dir / "issues_by_created.json", meta, "issues_by_created",
            client, "/search/issues",
            {"q": f"repo:{repo} is:issue created:{date_range}"},
        )
        # 5. PRs updated in window (REST, for reviews enumeration)
        prs_updated = _fetch_prs_updated(client, repo, since, until)
        (repo_dir / "prs_updated.json").write_text(json.dumps(prs_updated))
        meta["prs_updated"] = {"total_count": len(prs_updated), "truncated": False}

        # 6. reviews per PR
        reviews_dir = repo_dir / "reviews"
        reviews_dir.mkdir(exist_ok=True)
        total_reviews = 0
        for pr in prs_updated:
            number = pr.get("number")
            if not isinstance(number, int):
                continue
            pages: list[dict] = []
            for page in client.get_paginated(
                f"/repos/{repo}/pulls/{number}/reviews",
                {"per_page": 100},
            ):
                pages.extend(page)
            (reviews_dir / f"{number}.json").write_text(json.dumps(pages))
            total_reviews += len(pages)
        meta["reviews"] = {"total_count": total_reviews, "truncated": False}

        # 7. review comments (repo-wide)
        review_comments = _paginate_until_before(
            client,
            f"/repos/{repo}/pulls/comments",
            since,
            ts_key="created_at",
            params={"sort": "created", "direction": "desc", "per_page": 100},
        )
        # Also filter out anything after `until` (we sort desc, so we might grab those).
        review_comments = [c for c in review_comments if c.get("created_at") and c["created_at"][:10] <= until.isoformat()]
        (repo_dir / "review_comments.json").write_text(json.dumps(review_comments))
        meta["review_comments"] = {"total_count": len(review_comments), "truncated": False}

        # 8. issue comments (repo-wide)
        issue_comments = _paginate_until_before(
            client,
            f"/repos/{repo}/issues/comments",
            since,
            ts_key="created_at",
            params={"sort": "created", "direction": "desc", "per_page": 100},
        )
        issue_comments = [c for c in issue_comments if c.get("created_at") and c["created_at"][:10] <= until.isoformat()]
        (repo_dir / "issue_comments.json").write_text(json.dumps(issue_comments))
        meta["issue_comments"] = {"total_count": len(issue_comments), "truncated": False}

    except NotFoundError:
        _write_error(repo_dir, "not_found")
        return
    except RateLimitError as exc:
        _write_error(repo_dir, f"rate_limited: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        print(f"fetch error for {repo}: {exc}", file=sys.stderr)
        _write_error(repo_dir, f"error: {exc}")
        return

    (repo_dir / "_meta.json").write_text(json.dumps(meta))


def _write_search(
    out_path: Path,
    meta: dict,
    key: str,
    client: GitHubClient,
    path: str,
    params: dict,
) -> None:
    items: list[dict] = []
    total = 0
    truncated = False
    for page in client.search_paginated(path, params):
        items.extend(page.items)
        total = max(total, page.total_count)
    if total > 1000:
        truncated = True
        print(
            f"warning: search {path} q={params.get('q')!r} total_count={total} exceeds 1000-hit cap; results truncated",
            file=sys.stderr,
        )
    out_path.write_text(json.dumps(items))
    meta[key] = {"total_count": total, "truncated": truncated}


def _fetch_prs_updated(client: GitHubClient, repo: str, since: date, until: date) -> list[dict]:
    prs: list[dict] = []
    for page in client.get_paginated(
        f"/repos/{repo}/pulls",
        {"state": "all", "sort": "updated", "direction": "desc", "per_page": 100},
    ):
        stop = False
        for pr in page:
            updated = (pr.get("updated_at") or "")[:10]
            if updated and updated < since.isoformat():
                stop = True
                break
            if updated > until.isoformat():
                continue
            prs.append(pr)
        if stop:
            break
    return prs


def _paginate_until_before(
    client: GitHubClient,
    path: str,
    since: date,
    *,
    ts_key: str,
    params: dict,
) -> list[dict]:
    out: list[dict] = []
    for page in client.get_paginated(path, params):
        stop = False
        for item in page:
            ts = (item.get(ts_key) or "")[:10]
            if ts and ts < since.isoformat():
                stop = True
                break
            out.append(item)
        if stop:
            break
    return out


def _write_error(repo_dir: Path, reason: str) -> None:
    for child in repo_dir.iterdir():
        if child.is_dir():
            for grandchild in child.iterdir():
                grandchild.unlink()
            child.rmdir()
        else:
            child.unlink()
    (repo_dir / "_meta.json").write_text(json.dumps({"error": reason}))


def _months_between(since: date, today: date) -> list[str]:
    if since > today:
        return []
    out: list[str] = []
    y, m = since.year, since.month
    end_y, end_m = today.year, today.month
    while (y, m) <= (end_y, end_m):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            m = 1
            y += 1
    return out


def _month_bounds(month: str, today: date) -> tuple[date, date]:
    year_s, mon_s = month.split("-", 1)
    year, mon = int(year_s), int(mon_s)
    first = date(year, mon, 1)
    if (year, mon) == (today.year, today.month):
        return first, today
    last_day = monthrange(year, mon)[1]
    return first, date(year, mon, last_day)
