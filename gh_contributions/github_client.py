"""GitHub REST client: auth, pagination, backoff, retry.

No knowledge of metrics; endpoints and parameters are the caller's responsibility.
"""

from __future__ import annotations

import re
import sys
import time
from typing import Iterator, NamedTuple

import requests


BASE_URL = "https://api.github.com"
_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')
_SEARCH_CAP = 1000


class AuthError(Exception):
    """401 / bad token."""


class NotFoundError(Exception):
    """404 on a resource."""


class RateLimitError(Exception):
    """Exceeded retry budget for rate-limited responses."""


class SearchPage(NamedTuple):
    items: list[dict]
    total_count: int


class GitHubClient:
    def __init__(self, token: str, *, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()
        self._session.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": f"Bearer {token}",
            "User-Agent": "gh_contributions/0.1",
        })

    def get_paginated(self, path: str, params: dict) -> Iterator[list[dict]]:
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        current_params = dict(params)
        while True:
            resp = self._get(url, current_params)
            data = resp.json()
            if isinstance(data, list):
                yield data
            else:
                # Some endpoints return {"items": [...]} etc.; caller uses search_paginated for those.
                yield []
            next_url = _next_link(resp.headers.get("Link", ""))
            if not next_url:
                return
            url = next_url
            current_params = {}  # `Link` next URL already includes query params.

    def search_paginated(self, path: str, params: dict) -> Iterator[SearchPage]:
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        current_params = dict(params)
        current_params.setdefault("per_page", 100)
        seen = 0
        while True:
            resp = self._get(url, current_params)
            body = resp.json()
            items = body.get("items", []) if isinstance(body, dict) else []
            total = body.get("total_count", 0) if isinstance(body, dict) else 0
            yield SearchPage(items=items, total_count=int(total))
            seen += len(items)
            if seen >= _SEARCH_CAP:
                return
            next_url = _next_link(resp.headers.get("Link", ""))
            if not next_url:
                return
            url = next_url
            current_params = {}

    def _get(self, url: str, params: dict) -> requests.Response:
        attempts = 0
        while True:
            resp = self._session.get(url, params=params, timeout=30)
            if resp.status_code == 401:
                raise AuthError("401 Unauthorized — check GITHUB_TOKEN")
            if resp.status_code == 404:
                raise NotFoundError(f"404 Not Found: {url}")
            if resp.status_code == 403 and _is_primary_rate_limited(resp):
                _sleep_until_reset(resp)
                continue
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "60"))
                if attempts >= 1:
                    raise RateLimitError(f"429 after retry: {url}")
                attempts += 1
                _sleep(retry_after)
                continue
            if 500 <= resp.status_code < 600:
                if attempts >= 1:
                    resp.raise_for_status()
                attempts += 1
                _sleep(2)
                continue
            resp.raise_for_status()
            _preemptive_sleep_if_low(resp)
            return resp


def _next_link(link_header: str) -> str | None:
    m = _NEXT_RE.search(link_header or "")
    return m.group(1) if m else None


def _is_primary_rate_limited(resp: requests.Response) -> bool:
    return resp.headers.get("X-RateLimit-Remaining") == "0"


def _preemptive_sleep_if_low(resp: requests.Response) -> None:
    try:
        remaining = int(resp.headers.get("X-RateLimit-Remaining", "9999"))
    except ValueError:
        return
    if remaining < 5:
        _sleep_until_reset(resp)


def _sleep_until_reset(resp: requests.Response) -> None:
    try:
        reset = int(resp.headers.get("X-RateLimit-Reset", "0"))
    except ValueError:
        reset = 0
    delay = max(1, reset - int(time.time()))
    print(f"rate limit: sleeping {delay}s until reset", file=sys.stderr)
    _sleep(delay)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)
