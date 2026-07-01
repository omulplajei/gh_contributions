# Extraction Scripts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `gh_contributions` Python package that reads `config.yml`, fetches GitHub data per the metric catalog, and writes `out/<run>/metrics.json`.

**Architecture:** Five focused modules with a disk boundary between fetch and compute — `config.py` (validation), `github_client.py` (HTTP glue), `fetch.py` (writes raw pages), `metrics.py` (pure computation from disk), `run.py` (orchestration). Pure logic is TDD'd; HTTP/I/O glue is validated by real runs.

**Tech Stack:** Python 3, `requests`, `PyYAML`, `pytest`. No other deps.

## Global Constraints

Copied verbatim from [extraction-scripts design](../specs/2026-07-01-extraction-scripts-design.md) and its parents:

- Config schema: `usernames` (non-empty list of strings), `repos` (list of `owner/repo`, may be empty), `since`/`until` (`YYYY-MM-DD`, `until >= since`), `metrics` (non-empty subset of `{authoring, collaboration, team_share}`).
- `config.yml` is git-ignored — never committed, never `git add -f`'d.
- `out/` is git-ignored — added to `.gitignore` in Task 1.
- Auth: `GITHUB_TOKEN` env var. Missing → exit 2.
- Timezone: UTC only. `since`/`until` are inclusive calendar-day bounds converted to `[00:00:00Z, 23:59:59Z]`.
- Bots (`*[bot]`, or any login not in `config.usernames`) are excluded from `per_user` but included in `team_share` denominators.
- Reviews states counted: `APPROVED`, `CHANGES_REQUESTED`, `COMMENTED` only. `DISMISSED` and `PENDING` ignored.
- Self-reviews counted; merge commits included; all branches (Search API `search/commits`).
- Rate limit handling: sleep until `X-RateLimit-Reset` when `X-RateLimit-Remaining < 5`; honor `Retry-After` on 429; one retry on 5xx.
- Exit codes: 0 any repo produced metrics, 1 all-repo failure, 2 config/auth error.
- Every `raw/*.json` (except `_meta.json`) is a JSON array of the concatenated pages. Empty results = `[]` still written.
- Search API results cap at 1000; when `total_count > 1000`, `_meta.json` records `truncated: true`. No fallback (deferred).
- TDD for `config.py` and `metrics.py`. `github_client.py`, `fetch.py`, `run.py` are integration glue — no unit tests.

---

## Starting State

- HEAD is `9a51f27 docs: add extraction scripts design spec` (on top of `30192a4`, `2f30321`, `6a7eb18`).
- Working tree clean except for the git-ignored `config.yml`.
- No `gh_contributions/` package, no `tests/`, no `requirements.txt` yet.

Verify:

```bash
git log --oneline -1
test ! -e gh_contributions && test ! -e tests && test ! -e requirements.txt && echo OK
git status --short
```

Expected: HEAD is `9a51f27`, then `OK`, then empty status.

---

### Task 1: Project scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `gh_contributions/__init__.py` (empty)
- Create: `tests/__init__.py` (empty)
- Modify: `.gitignore` (add `out/` entry)

**Interfaces:**
- Consumes: nothing.
- Produces: an importable empty `gh_contributions` package; a `tests/` directory pytest will discover; pinned dependencies; `out/` git-ignored so later tasks can write there without polluting `git status`.

- [ ] **Step 1: Create `requirements.txt`**

```
requests>=2.31,<3
PyYAML>=6.0,<7
pytest>=8.0,<9
```

- [ ] **Step 2: Create empty package files**

```bash
mkdir -p gh_contributions tests
: > gh_contributions/__init__.py
: > tests/__init__.py
```

Verify:
```bash
test -f gh_contributions/__init__.py && test -f tests/__init__.py && echo OK
```
Expected: `OK`.

- [ ] **Step 3: Add `out/` to `.gitignore`**

Append to `.gitignore`:

```
# Analyzer output directory
out/
```

Verify:
```bash
tail -n 3 .gitignore
grep -c '^out/$' .gitignore
```
Expected: last three lines include the comment and `out/`; grep count is `1`.

- [ ] **Step 4: Install deps into the current env (verification only)**

```bash
python3 -m pip install -r requirements.txt
python3 -c "import requests, yaml, pytest; print('OK')"
```
Expected: `OK`.

If pip requires `--user` or a venv on this system, that's a local operator concern — install however you normally do, just confirm the three imports work.

- [ ] **Step 5: Verify pytest discovers zero tests without error**

```bash
python3 -m pytest tests/ -q
```
Expected: `no tests ran in ...s` (exit code 5 from pytest, which is fine — no failure).

- [ ] **Step 6: Commit**

```bash
git add requirements.txt gh_contributions/__init__.py tests/__init__.py .gitignore
git commit -m "chore: scaffold gh_contributions package and pin deps"
git log --oneline -1
```
Expected: new commit on top of `9a51f27`.

---

### Task 2: `config.py` — load and validate `config.yml`

**Files:**
- Create: `gh_contributions/config.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Config` dataclass: `usernames: list[str]`, `repos: list[str]`, `since: datetime.date`, `until: datetime.date`, `metrics: list[str]`.
  - `ConfigError(Exception)`.
  - `load_config(path: str) -> Config` — raises `ConfigError` on any validation failure; prints a warning to `sys.stderr` when `repos` is empty.

TDD. Every rule is one test.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config.py`:

```python
import io
import sys
import textwrap
from datetime import date
from pathlib import Path

import pytest

from gh_contributions.config import Config, ConfigError, load_config


VALID_YAML = textwrap.dedent("""\
    usernames:
      - alice
      - bob
    repos:
      - acme/api
    since: 2026-01-01
    until: 2026-06-30
    metrics:
      - authoring
      - collaboration
      - team_share
""")


def _write(tmp_path: Path, body: str) -> str:
    p = tmp_path / "config.yml"
    p.write_text(body)
    return str(p)


def test_load_happy_path(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, VALID_YAML))
    assert cfg == Config(
        usernames=["alice", "bob"],
        repos=["acme/api"],
        since=date(2026, 1, 1),
        until=date(2026, 6, 30),
        metrics=["authoring", "collaboration", "team_share"],
    )


def test_empty_usernames_errors(tmp_path: Path) -> None:
    body = VALID_YAML.replace("usernames:\n  - alice\n  - bob\n", "usernames: []\n")
    with pytest.raises(ConfigError, match="usernames"):
        load_config(_write(tmp_path, body))


def test_empty_metrics_errors(tmp_path: Path) -> None:
    body = VALID_YAML.replace(
        "metrics:\n  - authoring\n  - collaboration\n  - team_share\n",
        "metrics: []\n",
    )
    with pytest.raises(ConfigError, match="metrics"):
        load_config(_write(tmp_path, body))


def test_unknown_metric_errors(tmp_path: Path) -> None:
    body = VALID_YAML.replace("- authoring", "- bogus_metric")
    with pytest.raises(ConfigError, match="bogus_metric"):
        load_config(_write(tmp_path, body))


def test_until_before_since_errors(tmp_path: Path) -> None:
    body = VALID_YAML.replace("since: 2026-01-01", "since: 2026-07-01")
    with pytest.raises(ConfigError, match="until"):
        load_config(_write(tmp_path, body))


def test_malformed_repo_errors(tmp_path: Path) -> None:
    body = VALID_YAML.replace("- acme/api", "- not-a-repo")
    with pytest.raises(ConfigError, match="not-a-repo"):
        load_config(_write(tmp_path, body))


def test_empty_repos_warns_not_errors(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    body = VALID_YAML.replace("repos:\n  - acme/api\n", "repos: []\n")
    cfg = load_config(_write(tmp_path, body))
    assert cfg.repos == []
    captured = capsys.readouterr()
    assert "repos" in captured.err.lower()
```

- [ ] **Step 2: Run tests — expect failure**

```bash
python3 -m pytest tests/test_config.py -v
```
Expected: import error / module not found for `gh_contributions.config`.

- [ ] **Step 3: Implement `config.py`**

Create `gh_contributions/config.py`:

```python
"""Load and validate config.yml for the contribution analyzer."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import date
from typing import Any

import yaml


ALLOWED_METRICS = {"authoring", "collaboration", "team_share"}
_REPO_RE = re.compile(r"^[^/\s]+/[^/\s]+$")


class ConfigError(ValueError):
    """Raised when config.yml fails validation."""


@dataclass(frozen=True)
class Config:
    usernames: list[str]
    repos: list[str]
    since: date
    until: date
    metrics: list[str]


def load_config(path: str) -> Config:
    with open(path, "r", encoding="utf-8") as fh:
        raw: Any = yaml.safe_load(fh)

    if not isinstance(raw, dict):
        raise ConfigError("config.yml top-level must be a mapping")

    usernames = _require_list_of_str(raw, "usernames")
    if not usernames:
        raise ConfigError("usernames must be a non-empty list")

    repos = _require_list_of_str(raw, "repos", allow_empty=True)
    for r in repos:
        if not _REPO_RE.match(r):
            raise ConfigError(f"repos entry must be 'owner/repo': got {r!r}")
    if not repos:
        print("warning: repos is empty; no repositories will be analyzed", file=sys.stderr)

    since = _require_date(raw, "since")
    until = _require_date(raw, "until")
    if until < since:
        raise ConfigError(f"until ({until}) must be >= since ({since})")

    metrics = _require_list_of_str(raw, "metrics")
    if not metrics:
        raise ConfigError("metrics must be a non-empty list")
    for m in metrics:
        if m not in ALLOWED_METRICS:
            raise ConfigError(
                f"metrics entry {m!r} not in allowed set "
                f"{sorted(ALLOWED_METRICS)}"
            )

    return Config(
        usernames=usernames,
        repos=repos,
        since=since,
        until=until,
        metrics=metrics,
    )


def _require_list_of_str(raw: dict, key: str, *, allow_empty: bool = False) -> list[str]:
    if key not in raw:
        raise ConfigError(f"missing required key: {key}")
    val = raw[key]
    if val is None and allow_empty:
        return []
    if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
        raise ConfigError(f"{key} must be a list of strings")
    return val


def _require_date(raw: dict, key: str) -> date:
    if key not in raw:
        raise ConfigError(f"missing required key: {key}")
    val = raw[key]
    if isinstance(val, date):
        return val
    raise ConfigError(f"{key} must be a YYYY-MM-DD date, got {val!r}")
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python3 -m pytest tests/test_config.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add gh_contributions/config.py tests/test_config.py
git commit -m "feat: config loader and validation"
```

---

### Task 3: `metrics.py` — authoring layer

**Files:**
- Create: `gh_contributions/metrics.py`
- Create: `tests/test_metrics.py`
- Create: `tests/fixtures/authoring/config.yml`
- Create: `tests/fixtures/authoring/raw/acme__api/{commits,prs_by_created,prs_by_merged,issues_by_created}.json`
- Create: `tests/fixtures/authoring/raw/acme__api/_meta.json`

**Interfaces:**
- Consumes: `Config` from Task 2.
- Produces:
  - `compute(raw_dir: pathlib.Path, config: Config) -> dict` — pure function; reads `raw_dir/<owner>__<name>/` for each `owner/name` in `config.repos`; returns the full output dict per the spec. In this task, `compute` populates only the `authoring` layer. `collaboration` and `team_share` blocks are added in Tasks 4 and 5.
  - Output shape at repo level: `{"per_user": {login: {"authoring": {...}}}, "team_share": null, "truncated": {...}, "error": null}`.

TDD. Start with authoring-only tests.

- [ ] **Step 1: Write fixture files**

Create `tests/fixtures/authoring/config.yml`:

```yaml
usernames:
  - alice
  - bob
repos:
  - acme/api
since: 2026-01-01
until: 2026-06-30
metrics:
  - authoring
```

Create `tests/fixtures/authoring/raw/acme__api/commits.json`:

```json
[
  {"sha": "a1", "author": {"login": "alice"}, "commit": {"author": {"date": "2026-02-01T10:00:00Z"}}},
  {"sha": "a2", "author": {"login": "alice"}, "commit": {"author": {"date": "2026-03-15T10:00:00Z"}}},
  {"sha": "b1", "author": {"login": "bob"},   "commit": {"author": {"date": "2026-04-01T10:00:00Z"}}},
  {"sha": "x1", "author": {"login": "eve"},   "commit": {"author": {"date": "2026-05-01T10:00:00Z"}}},
  {"sha": "d1", "author": {"login": "dependabot[bot]"}, "commit": {"author": {"date": "2026-06-01T10:00:00Z"}}}
]
```

Create `tests/fixtures/authoring/raw/acme__api/prs_by_created.json`:

```json
[
  {"number": 1, "user": {"login": "alice"}, "created_at": "2026-02-10T10:00:00Z"},
  {"number": 2, "user": {"login": "alice"}, "created_at": "2026-03-20T10:00:00Z"},
  {"number": 3, "user": {"login": "bob"},   "created_at": "2026-04-05T10:00:00Z"},
  {"number": 9, "user": {"login": "eve"},   "created_at": "2026-05-05T10:00:00Z"}
]
```

Create `tests/fixtures/authoring/raw/acme__api/prs_by_merged.json`:

```json
[
  {"number": 1, "user": {"login": "alice"}, "merged_at": "2026-02-12T10:00:00Z"},
  {"number": 3, "user": {"login": "bob"},   "merged_at": "2026-04-07T10:00:00Z"}
]
```

Create `tests/fixtures/authoring/raw/acme__api/issues_by_created.json`:

```json
[
  {"number": 10, "user": {"login": "alice"}, "created_at": "2026-02-15T10:00:00Z"},
  {"number": 11, "user": {"login": "eve"},   "created_at": "2026-03-01T10:00:00Z"}
]
```

Create `tests/fixtures/authoring/raw/acme__api/_meta.json`:

```json
{
  "commits":            {"total_count": 5,   "truncated": false},
  "prs_by_created":     {"total_count": 4,   "truncated": false},
  "prs_by_merged":      {"total_count": 2,   "truncated": false},
  "issues_by_created":  {"total_count": 2,   "truncated": false}
}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_metrics.py`:

```python
from pathlib import Path

import pytest

from gh_contributions.config import load_config
from gh_contributions.metrics import compute


FIXTURES = Path(__file__).parent / "fixtures"


def _load(fixture: str):
    cfg = load_config(str(FIXTURES / fixture / "config.yml"))
    return compute(FIXTURES / fixture / "raw", cfg)


def test_authoring_counts_per_user() -> None:
    out = _load("authoring")
    repo = out["repos"]["acme/api"]
    assert repo["error"] is None
    users = repo["per_user"]
    # Only team users are keys; eve and dependabot[bot] are absent.
    assert set(users) == {"alice", "bob"}
    assert users["alice"]["authoring"] == {
        "commits": 2,
        "pull_requests_opened": 2,
        "pull_requests_merged": 1,
        "issues_opened": 1,
    }
    assert users["bob"]["authoring"] == {
        "commits": 1,
        "pull_requests_opened": 1,
        "pull_requests_merged": 1,
        "issues_opened": 0,
    }


def test_authoring_only_layer_no_team_share_no_collab() -> None:
    out = _load("authoring")
    repo = out["repos"]["acme/api"]
    assert repo["team_share"] is None
    # Users don't have a collaboration block when only authoring is enabled.
    for u in repo["per_user"].values():
        assert "collaboration" not in u


def test_run_metadata_present() -> None:
    out = _load("authoring")
    assert out["run"]["since"] == "2026-01-01"
    assert out["run"]["until"] == "2026-06-30"
    assert out["run"]["metrics_layers"] == ["authoring"]
    assert "generated_at" in out["run"]
```

- [ ] **Step 3: Run tests — expect failure**

```bash
python3 -m pytest tests/test_metrics.py -v
```
Expected: `ModuleNotFoundError: gh_contributions.metrics`.

- [ ] **Step 4: Implement `metrics.py` (authoring layer only)**

Create `gh_contributions/metrics.py`:

```python
"""Pure computation of team-activity metrics from on-disk raw pages."""

from __future__ import annotations

import json
from datetime import datetime, timezone
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

    if "authoring" in config.metrics:
        _apply_authoring(repo_dir, config, per_user, truncated)

    return {
        "per_user": per_user,
        "team_share": None,
        "truncated": truncated,
        "error": None,
    }


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

    meta = _read_json(repo_dir / "_meta.json", default={})
    for src_key in ("commits", "prs_by_created", "prs_by_merged", "issues_by_created"):
        if isinstance(meta, dict) and meta.get(src_key, {}).get("truncated"):
            truncated[src_key] = True


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
```

- [ ] **Step 5: Run tests — expect pass**

```bash
python3 -m pytest tests/test_metrics.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add gh_contributions/metrics.py tests/test_metrics.py tests/fixtures/authoring/
git commit -m "feat: metrics authoring layer with fixture tests"
```

---

### Task 4: `metrics.py` — collaboration layer

**Files:**
- Modify: `gh_contributions/metrics.py`
- Modify: `tests/test_metrics.py`
- Create: `tests/fixtures/collaboration/config.yml`
- Create: `tests/fixtures/collaboration/raw/acme__api/{prs_updated,review_comments,issue_comments}.json`
- Create: `tests/fixtures/collaboration/raw/acme__api/reviews/{1,2,3}.json`
- Create: `tests/fixtures/collaboration/raw/acme__api/_meta.json`

**Interfaces:**
- Consumes: `Config`, output shape from Task 3.
- Produces: `compute` now also populates `per_user[login]["collaboration"]` with `reviews_given` (dict keyed by state), `review_comments`, `pr_conversation_comments`, `issue_comments`, `cross_team_reviews`.

- [ ] **Step 1: Create fixture files**

`tests/fixtures/collaboration/config.yml`:

```yaml
usernames:
  - alice
  - bob
repos:
  - acme/api
since: 2026-01-01
until: 2026-06-30
metrics:
  - collaboration
```

`tests/fixtures/collaboration/raw/acme__api/prs_updated.json`:

```json
[
  {"number": 1, "user": {"login": "alice"}},
  {"number": 2, "user": {"login": "eve"}},
  {"number": 3, "user": {"login": "bob"}}
]
```

`tests/fixtures/collaboration/raw/acme__api/reviews/1.json`:

```json
[
  {"user": {"login": "alice"}, "state": "APPROVED",          "submitted_at": "2026-02-01T10:00:00Z"},
  {"user": {"login": "bob"},   "state": "CHANGES_REQUESTED", "submitted_at": "2026-02-02T10:00:00Z"},
  {"user": {"login": "bob"},   "state": "DISMISSED",         "submitted_at": "2026-02-03T10:00:00Z"}
]
```

`tests/fixtures/collaboration/raw/acme__api/reviews/2.json`:

```json
[
  {"user": {"login": "alice"}, "state": "COMMENTED", "submitted_at": "2026-03-10T10:00:00Z"},
  {"user": {"login": "alice"}, "state": "APPROVED",  "submitted_at": "2026-03-11T10:00:00Z"},
  {"user": {"login": "eve"},   "state": "APPROVED",  "submitted_at": "2026-03-12T10:00:00Z"}
]
```

`tests/fixtures/collaboration/raw/acme__api/reviews/3.json`:

```json
[
  {"user": {"login": "bob"},   "state": "APPROVED",  "submitted_at": "2026-04-01T10:00:00Z"},
  {"user": {"login": "alice"}, "state": "PENDING",   "submitted_at": "2026-04-02T10:00:00Z"}
]
```

`tests/fixtures/collaboration/raw/acme__api/review_comments.json`:

```json
[
  {"user": {"login": "alice"}, "created_at": "2026-02-05T10:00:00Z"},
  {"user": {"login": "alice"}, "created_at": "2026-02-06T10:00:00Z"},
  {"user": {"login": "alice"}, "created_at": "2025-12-31T10:00:00Z"},
  {"user": {"login": "bob"},   "created_at": "2026-03-01T10:00:00Z"},
  {"user": {"login": "eve"},   "created_at": "2026-03-01T10:00:00Z"}
]
```

`tests/fixtures/collaboration/raw/acme__api/issue_comments.json`:

```json
[
  {"user": {"login": "alice"}, "issue_url": "https://api.github.com/repos/acme/api/issues/1",  "created_at": "2026-02-10T10:00:00Z"},
  {"user": {"login": "alice"}, "issue_url": "https://api.github.com/repos/acme/api/issues/2",  "created_at": "2026-02-11T10:00:00Z"},
  {"user": {"login": "bob"},   "issue_url": "https://api.github.com/repos/acme/api/issues/50", "created_at": "2026-03-01T10:00:00Z"},
  {"user": {"login": "bob"},   "issue_url": "https://api.github.com/repos/acme/api/issues/51", "created_at": "2026-03-02T10:00:00Z"}
]
```

Note: issues #1, #2, #3 are PRs (in `prs_updated.json`); #50 and #51 are non-PRs. So alice has 2 PR-conversation comments; bob has 2 issue comments.

`tests/fixtures/collaboration/raw/acme__api/_meta.json`:

```json
{
  "prs_updated":     {"total_count": 3, "truncated": false},
  "review_comments": {"total_count": 5, "truncated": false},
  "issue_comments":  {"total_count": 4, "truncated": false},
  "reviews":         {"total_count": 8, "truncated": false}
}
```

- [ ] **Step 2: Extend `tests/test_metrics.py`**

Append these tests to `tests/test_metrics.py`:

```python
def test_collaboration_reviews_by_state() -> None:
    out = _load("collaboration")
    users = out["repos"]["acme/api"]["per_user"]
    # PENDING and DISMISSED are ignored.
    assert users["alice"]["collaboration"]["reviews_given"] == {
        "APPROVED": 2, "CHANGES_REQUESTED": 0, "COMMENTED": 1,
    }
    assert users["bob"]["collaboration"]["reviews_given"] == {
        "APPROVED": 1, "CHANGES_REQUESTED": 1, "COMMENTED": 0,
    }


def test_collaboration_cross_team_reviews() -> None:
    out = _load("collaboration")
    users = out["repos"]["acme/api"]["per_user"]
    # PR 2 is authored by eve (not in team). alice reviewed PR 2 twice (COMMENTED, APPROVED).
    # PR 1 authored by alice (team), so bob's review on PR 1 is not cross-team.
    # PR 3 authored by bob (team), no cross-team review there.
    assert users["alice"]["collaboration"]["cross_team_reviews"] == 2
    assert users["bob"]["collaboration"]["cross_team_reviews"] == 0


def test_collaboration_review_comments_windowed() -> None:
    out = _load("collaboration")
    users = out["repos"]["acme/api"]["per_user"]
    # alice has 3 total; 1 is before window (2025-12-31) -> 2 counted. eve excluded (not team).
    assert users["alice"]["collaboration"]["review_comments"] == 2
    assert users["bob"]["collaboration"]["review_comments"] == 1


def test_collaboration_pr_vs_issue_comment_split() -> None:
    out = _load("collaboration")
    users = out["repos"]["acme/api"]["per_user"]
    # alice: both comments are on PR #1 and #2 -> pr_conversation_comments=2, issue_comments=0
    # bob: comments on #50 and #51 which are not in prs_updated -> issue_comments=2
    assert users["alice"]["collaboration"]["pr_conversation_comments"] == 2
    assert users["alice"]["collaboration"]["issue_comments"] == 0
    assert users["bob"]["collaboration"]["pr_conversation_comments"] == 0
    assert users["bob"]["collaboration"]["issue_comments"] == 2
```

- [ ] **Step 3: Run tests — expect failure**

```bash
python3 -m pytest tests/test_metrics.py -v
```
Expected: 4 new failures (KeyError on `collaboration`).

- [ ] **Step 4: Extend `metrics.py` with the collaboration layer**

Add these imports to the top of `gh_contributions/metrics.py`:

```python
from datetime import date, datetime, time, timezone
```

Replace the plain `from datetime import datetime, timezone` line with the above.

Then add the collaboration constants and helpers. Insert after `_read_json`:

```python
_REVIEW_STATES = ("APPROVED", "CHANGES_REQUESTED", "COMMENTED")


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

    meta = _read_json(repo_dir / "_meta.json", default={})
    for src_key in ("prs_updated", "review_comments", "issue_comments", "reviews"):
        if isinstance(meta, dict) and meta.get(src_key, {}).get("truncated"):
            truncated[src_key] = True


def _parent_number(issue_url: str | None) -> int | None:
    if not issue_url:
        return None
    tail = issue_url.rstrip("/").rsplit("/", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return None
```

And wire it into `_compute_repo` — add the collaboration branch right after the authoring branch:

```python
    if "authoring" in config.metrics:
        _apply_authoring(repo_dir, config, per_user, truncated)

    if "collaboration" in config.metrics:
        _apply_collaboration(repo_dir, config, per_user, truncated)
```

- [ ] **Step 5: Run tests — expect all pass**

```bash
python3 -m pytest tests/test_metrics.py -v
```
Expected: 7 passed (3 from Task 3 + 4 new).

- [ ] **Step 6: Commit**

```bash
git add gh_contributions/metrics.py tests/test_metrics.py tests/fixtures/collaboration/
git commit -m "feat: metrics collaboration layer"
```

---

### Task 5: `metrics.py` — team_share layer, truncation, errors, layer selection

**Files:**
- Modify: `gh_contributions/metrics.py`
- Modify: `tests/test_metrics.py`
- Create: `tests/fixtures/team_share/config.yml`
- Create: `tests/fixtures/team_share/raw/acme__api/{commits,prs_by_created,prs_updated,review_comments,issue_comments}.json`
- Create: `tests/fixtures/team_share/raw/acme__api/reviews/{1,2}.json`
- Create: `tests/fixtures/team_share/raw/acme__api/_meta.json`
- Create: `tests/fixtures/empty_repo/config.yml`
- Create: `tests/fixtures/empty_repo/raw/acme__api/_meta.json` (with `error`)
- Create: `tests/fixtures/truncated/config.yml`
- Create: `tests/fixtures/truncated/raw/acme__api/{commits.json,_meta.json}`

**Interfaces:**
- Consumes: `Config`, output shape from Tasks 3–4.
- Produces: `compute` output has a populated `team_share` block per repo when `team_share` is enabled; `truncated` map reflects any endpoint's cap; `error` set when `_meta.json` records a top-level fetch error; per-layer selection controls what appears.

- [ ] **Step 1: Create team_share fixtures**

`tests/fixtures/team_share/config.yml`:

```yaml
usernames:
  - alice
  - bob
repos:
  - acme/api
since: 2026-01-01
until: 2026-06-30
metrics:
  - team_share
```

`tests/fixtures/team_share/raw/acme__api/commits.json`:

```json
[
  {"sha": "a1", "author": {"login": "alice"},            "commit": {"author": {"date": "2026-02-01T10:00:00Z"}}},
  {"sha": "a2", "author": {"login": "alice"},            "commit": {"author": {"date": "2026-02-02T10:00:00Z"}}},
  {"sha": "b1", "author": {"login": "bob"},              "commit": {"author": {"date": "2026-02-03T10:00:00Z"}}},
  {"sha": "b2", "author": {"login": "bob"},              "commit": {"author": {"date": "2026-02-04T10:00:00Z"}}},
  {"sha": "e1", "author": {"login": "eve"},              "commit": {"author": {"date": "2026-02-05T10:00:00Z"}}},
  {"sha": "e2", "author": {"login": "eve"},              "commit": {"author": {"date": "2026-02-06T10:00:00Z"}}},
  {"sha": "e3", "author": {"login": "eve"},              "commit": {"author": {"date": "2026-02-07T10:00:00Z"}}},
  {"sha": "d1", "author": {"login": "dependabot[bot]"}, "commit": {"author": {"date": "2026-02-08T10:00:00Z"}}},
  {"sha": "d2", "author": {"login": "dependabot[bot]"}, "commit": {"author": {"date": "2026-02-09T10:00:00Z"}}},
  {"sha": "d3", "author": {"login": "dependabot[bot]"}, "commit": {"author": {"date": "2026-02-10T10:00:00Z"}}}
]
```

Team = 4 commits, everyone = 10, share = 0.4.

`tests/fixtures/team_share/raw/acme__api/prs_by_created.json`:

```json
[
  {"number": 1, "user": {"login": "alice"}, "created_at": "2026-02-01T10:00:00Z"},
  {"number": 2, "user": {"login": "eve"},   "created_at": "2026-02-02T10:00:00Z"}
]
```

`tests/fixtures/team_share/raw/acme__api/prs_updated.json`:

```json
[
  {"number": 1, "user": {"login": "alice"}},
  {"number": 2, "user": {"login": "eve"}}
]
```

`tests/fixtures/team_share/raw/acme__api/reviews/1.json`:

```json
[
  {"user": {"login": "bob"}, "state": "APPROVED", "submitted_at": "2026-02-05T10:00:00Z"},
  {"user": {"login": "eve"}, "state": "APPROVED", "submitted_at": "2026-02-06T10:00:00Z"}
]
```

`tests/fixtures/team_share/raw/acme__api/reviews/2.json`:

```json
[
  {"user": {"login": "alice"}, "state": "APPROVED", "submitted_at": "2026-02-07T10:00:00Z"}
]
```

Team reviews = 2 (bob + alice), everyone = 3, share = 2/3.

`tests/fixtures/team_share/raw/acme__api/review_comments.json`:

```json
[
  {"user": {"login": "alice"}, "created_at": "2026-02-10T10:00:00Z"},
  {"user": {"login": "eve"},   "created_at": "2026-02-11T10:00:00Z"}
]
```

`tests/fixtures/team_share/raw/acme__api/issue_comments.json`:

```json
[
  {"user": {"login": "alice"}, "issue_url": "https://api.github.com/repos/acme/api/issues/1",  "created_at": "2026-02-12T10:00:00Z"},
  {"user": {"login": "bob"},   "issue_url": "https://api.github.com/repos/acme/api/issues/99", "created_at": "2026-02-13T10:00:00Z"}
]
```

Team comments total: 1 (review) + 1 (PR-conv) + 1 (issue) = 3. Everyone: 2 (review) + 2 (issue_comments) = 4. Share = 3/4 = 0.75.

`tests/fixtures/team_share/raw/acme__api/_meta.json`:

```json
{
  "commits":         {"total_count": 10, "truncated": false},
  "prs_by_created":  {"total_count": 2,  "truncated": false},
  "prs_updated":     {"total_count": 2,  "truncated": false},
  "reviews":         {"total_count": 3,  "truncated": false},
  "review_comments": {"total_count": 2,  "truncated": false},
  "issue_comments":  {"total_count": 2,  "truncated": false}
}
```

- [ ] **Step 2: Create empty-repo (error) and truncated fixtures**

`tests/fixtures/empty_repo/config.yml`:

```yaml
usernames:
  - alice
repos:
  - acme/api
since: 2026-01-01
until: 2026-06-30
metrics:
  - authoring
```

`tests/fixtures/empty_repo/raw/acme__api/_meta.json`:

```json
{"error": "not_found"}
```

`tests/fixtures/truncated/config.yml`:

```yaml
usernames:
  - alice
repos:
  - acme/api
since: 2026-01-01
until: 2026-06-30
metrics:
  - authoring
  - team_share
```

`tests/fixtures/truncated/raw/acme__api/commits.json`:

```json
[
  {"sha": "a1", "author": {"login": "alice"}, "commit": {"author": {"date": "2026-02-01T10:00:00Z"}}}
]
```

`tests/fixtures/truncated/raw/acme__api/_meta.json`:

```json
{
  "commits": {"total_count": 1500, "truncated": true}
}
```

- [ ] **Step 3: Extend `tests/test_metrics.py`**

Append:

```python
import math


def test_team_share_happy_path() -> None:
    out = _load("team_share")
    share = out["repos"]["acme/api"]["team_share"]
    assert math.isclose(share["share_commits"], 4 / 10)
    assert math.isclose(share["share_pull_requests_opened"], 1 / 2)
    assert math.isclose(share["share_reviews_given"], 2 / 3)
    assert math.isclose(share["share_comments"], 3 / 4)


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
    for f in ("commits.json", "prs_by_created.json", "prs_updated.json",
              "review_comments.json", "issue_comments.json"):
        (repo_dir / f).write_text("[]")
    (repo_dir / "reviews").mkdir()

    out = compute(tmp_path, cfg)
    share = out["repos"]["acme/api"]["team_share"]
    assert share["share_commits"] is None
    assert share["share_pull_requests_opened"] is None
    assert share["share_reviews_given"] is None
    assert share["share_comments"] is None


def test_repo_error_propagates() -> None:
    out = _load("empty_repo")
    repo = out["repos"]["acme/api"]
    assert repo["error"] == "not_found"
    assert repo["per_user"] is None
    assert repo["team_share"] is None


def test_truncation_flag_propagates() -> None:
    out = _load("truncated")
    repo = out["repos"]["acme/api"]
    assert repo["truncated"].get("commits") is True


def test_layer_selection_authoring_only_omits_team_share() -> None:
    out = _load("authoring")  # config enables only "authoring"
    repo = out["repos"]["acme/api"]
    assert repo["team_share"] is None


def test_layer_selection_team_share_only_omits_per_user_details() -> None:
    out = _load("team_share")  # config enables only "team_share"
    per_user = out["repos"]["acme/api"]["per_user"]
    # Users appear as keys but with no authoring/collaboration blocks.
    assert "alice" in per_user
    assert per_user["alice"] == {}
```

- [ ] **Step 4: Run tests — expect failure**

```bash
python3 -m pytest tests/test_metrics.py -v
```
Expected: `test_team_share_*`, `test_repo_error_propagates`, `test_truncation_flag_propagates`, and both `test_layer_selection_*` fail.

- [ ] **Step 5: Extend `metrics.py`**

Add the team-share helper after `_apply_collaboration`:

```python
def _apply_team_share(repo_dir: Path, config: Config, out: dict) -> None:
    team = set(config.usernames)
    lo, hi = _window_bounds(config)

    def _ratio(team_n: int, total_n: int) -> float | None:
        if total_n == 0:
            return None
        return team_n / total_n

    # commits — Search results are already window-filtered by the query.
    commits = _read_json(repo_dir / "commits.json", default=[])
    total_commits = len(commits)
    team_commits = sum(1 for c in commits if _author_login(c, "commits.json") in team)

    # prs_by_created — window-filtered by query.
    prs_opened = _read_json(repo_dir / "prs_by_created.json", default=[])
    total_prs = len(prs_opened)
    team_prs = sum(1 for p in prs_opened if _author_login(p, "prs_by_created.json") in team)

    # reviews — sum across all PRs' reviews files, filter to counted states + window.
    total_reviews = 0
    team_reviews = 0
    reviews_dir = repo_dir / "reviews"
    if reviews_dir.is_dir():
        for review_file in sorted(reviews_dir.glob("*.json")):
            for r in _read_json(review_file, default=[]):
                if r.get("state") not in _REVIEW_STATES:
                    continue
                if not _in_window(r.get("submitted_at"), lo, hi):
                    continue
                total_reviews += 1
                if ((r.get("user") or {}).get("login")) in team:
                    team_reviews += 1

    # comments — review_comments + issue_comments (both PR-conv and issue), window-filtered.
    total_comments = 0
    team_comments = 0
    for src in ("review_comments.json", "issue_comments.json"):
        for c in _read_json(repo_dir / src, default=[]):
            if not _in_window(c.get("created_at"), lo, hi):
                continue
            total_comments += 1
            if ((c.get("user") or {}).get("login")) in team:
                team_comments += 1

    out["team_share"] = {
        "share_commits": _ratio(team_commits, total_commits),
        "share_pull_requests_opened": _ratio(team_prs, total_prs),
        "share_reviews_given": _ratio(team_reviews, total_reviews),
        "share_comments": _ratio(team_comments, total_comments),
    }
```

Wire into `_compute_repo`. Replace the existing body of `_compute_repo` with:

```python
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

    if "authoring" in config.metrics:
        _apply_authoring(repo_dir, config, per_user, truncated)

    if "collaboration" in config.metrics:
        _apply_collaboration(repo_dir, config, per_user, truncated)

    if "team_share" in config.metrics:
        _apply_team_share(repo_dir, config, out)

    return out
```

- [ ] **Step 6: Run tests — expect all pass**

```bash
python3 -m pytest tests/ -v
```
Expected: all tests pass (Task 2's 7 config tests + Task 3's 3 authoring tests + Task 4's 4 collaboration tests + Task 5's 6 new tests = 20 tests).

- [ ] **Step 7: Commit**

```bash
git add gh_contributions/metrics.py tests/test_metrics.py tests/fixtures/team_share/ tests/fixtures/empty_repo/ tests/fixtures/truncated/
git commit -m "feat: metrics team_share layer, truncation, errors, layer selection"
```

---

### Task 6: `github_client.py` — HTTP glue

**Files:**
- Create: `gh_contributions/github_client.py`

**Interfaces:**
- Consumes: `GITHUB_TOKEN` env var (via the caller).
- Produces:
  - `class GitHubClient(token: str)`.
  - `GitHubClient.get_paginated(path: str, params: dict) -> Iterator[list[dict]]` — yields the JSON body of each REST page. Detects last page via missing `Link: rel="next"`.
  - `GitHubClient.search_paginated(path: str, params: dict) -> Iterator[SearchPage]` — yields `SearchPage(items, total_count)`; stops at API's 1000-item cap.
  - `class NotFoundError(Exception)`, `class AuthError(Exception)`, `class RateLimitError(Exception)`.
  - `class SearchPage(NamedTuple)` with `items: list[dict]`, `total_count: int`.
- No unit tests (integration glue, validated by real runs).

- [ ] **Step 1: Implement**

Create `gh_contributions/github_client.py`:

```python
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
```

- [ ] **Step 2: Smoke import**

```bash
python3 -c "from gh_contributions.github_client import GitHubClient, SearchPage, AuthError, NotFoundError, RateLimitError; print('OK')"
```
Expected: `OK`.

- [ ] **Step 3: Confirm existing tests still green**

```bash
python3 -m pytest tests/ -q
```
Expected: all previous tests still pass.

- [ ] **Step 4: Commit**

```bash
git add gh_contributions/github_client.py
git commit -m "feat: GitHub REST client with pagination and backoff"
```

---

### Task 7: `fetch.py` — per-repo fetchers writing raw pages

**Files:**
- Create: `gh_contributions/fetch.py`

**Interfaces:**
- Consumes: `GitHubClient` from Task 6; `Config` from Task 2.
- Produces:
  - `fetch_repo(client: GitHubClient, repo: str, since: date, until: date, out_dir: pathlib.Path) -> None` — fetches each source **once**; writes concatenated pages to `out_dir/<owner>__<name>/*.json` and `_meta.json`. On repo-level failure (404 or exhausted retries), writes only `_meta.json` with `{"error": "<reason>"}` and returns without raising.
- No unit tests (integration glue).

- [ ] **Step 1: Implement**

Create `gh_contributions/fetch.py`:

```python
"""Per-repo fetchers. Write raw JSON pages to disk; no aggregation."""

from __future__ import annotations

import json
import sys
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
            updated = pr.get("updated_at", "")[:10]
            if updated < since.isoformat():
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
            ts = item.get(ts_key, "")[:10]
            if ts < since.isoformat():
                stop = True
                break
            out.append(item)
        if stop:
            break
    return out


def _write_error(repo_dir: Path, reason: str) -> None:
    (repo_dir / "_meta.json").write_text(json.dumps({"error": reason}))
```

- [ ] **Step 2: Smoke import**

```bash
python3 -c "from gh_contributions.fetch import fetch_repo; print('OK')"
```
Expected: `OK`.

- [ ] **Step 3: Confirm all tests still green**

```bash
python3 -m pytest tests/ -q
```
Expected: all previous tests pass.

- [ ] **Step 4: Commit**

```bash
git add gh_contributions/fetch.py
git commit -m "feat: fetch layer writes raw pages per repo"
```

---

### Task 8: `run.py` — orchestration + README

**Files:**
- Create: `gh_contributions/run.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `python -m gh_contributions.run` reads `./config.yml`, requires `GITHUB_TOKEN` env, creates `out/<UTC-timestamp>/`, fetches all repos, computes, writes `metrics.json`, exits with the codes from the spec.

- [ ] **Step 1: Implement `run.py`**

Create `gh_contributions/run.py`:

```python
"""Entry point: config -> fetch -> compute -> metrics.json."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import ConfigError, load_config
from .fetch import fetch_repo
from .github_client import AuthError, GitHubClient
from .metrics import compute


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    config_path = argv[0] if argv else "config.yml"

    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"config not found: {config_path}", file=sys.stderr)
        return 2

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN env var is required", file=sys.stderr)
        return 2

    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    out_dir = Path("out") / run_id
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    print(f"run dir: {out_dir}", file=sys.stderr)

    if not cfg.repos:
        print("no repos configured; writing empty metrics.json", file=sys.stderr)
        _write_metrics(out_dir, compute(raw_dir, cfg))
        return 0

    client = GitHubClient(token)
    ok_count = 0
    for repo in cfg.repos:
        print(f"fetching {repo}", file=sys.stderr)
        try:
            fetch_repo(client, repo, cfg.since, cfg.until, raw_dir)
        except AuthError as exc:
            print(f"auth failed: {exc}", file=sys.stderr)
            return 2
        # Any other failure was recorded in the repo's _meta.json by fetch_repo.
        meta_path = raw_dir / f"{repo.replace('/', '__')}" / "_meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                if not meta.get("error"):
                    ok_count += 1
            except json.JSONDecodeError:
                pass

    result = compute(raw_dir, cfg)
    _write_metrics(out_dir, result)

    if ok_count == 0:
        print("no repos produced metrics", file=sys.stderr)
        return 1
    return 0


def _write_metrics(out_dir: Path, result: dict) -> None:
    path = out_dir / "metrics.json"
    path.write_text(json.dumps(result, indent=2))
    print(f"wrote {path}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Update `README.md`**

Overwrite `README.md`:

```markdown
# gh_contributions

Extracts per-user, per-repo GitHub contribution metrics for a configured team over a date window.

## Configure

Create `config.yml` at repo root (git-ignored — never commit):

```yaml
usernames:
  - alice
  - bob
repos:
  - acme/api
since: 2026-01-01
until: 2026-06-30
metrics:
  - authoring
  - collaboration
  - team_share
```

Schema and validation rules: see [docs/superpowers/specs/2026-07-01-analysis-config-file-design.md](docs/superpowers/specs/2026-07-01-analysis-config-file-design.md) and the metric catalog in [docs/superpowers/specs/2026-07-01-team-activity-metrics-design.md](docs/superpowers/specs/2026-07-01-team-activity-metrics-design.md).

## Install

```bash
python3 -m pip install -r requirements.txt
```

## Run

```bash
export GITHUB_TOKEN=<personal access token with repo:read>
python3 -m gh_contributions.run
```

Output: `out/<UTC-timestamp>/metrics.json`. Raw API pages are kept under `out/<UTC-timestamp>/raw/` for audit and reruns.

## Test

```bash
python3 -m pytest -q
```
```

- [ ] **Step 3: Smoke test — invalid config exits 2**

```bash
python3 -m gh_contributions.run /nonexistent/path.yml
echo "exit=$?"
```
Expected: stderr message about config not found; `exit=2`.

- [ ] **Step 4: Smoke test — missing token exits 2**

```bash
env -u GITHUB_TOKEN python3 -m gh_contributions.run
echo "exit=$?"
```
Expected: stderr message about `GITHUB_TOKEN`; `exit=2`.

- [ ] **Step 5: Smoke test — empty repos writes empty metrics.json, exits 0**

Only run if the local `config.yml` has `repos: []` (which it does by default per current state):

```bash
GITHUB_TOKEN=dummy python3 -m gh_contributions.run
echo "exit=$?"
ls out/
```
Expected: stderr "no repos configured; writing empty metrics.json"; `exit=0`; a fresh timestamped dir under `out/` containing `metrics.json`.

Verify the JSON shape:
```bash
python3 -c "import json,glob; d=json.load(open(sorted(glob.glob('out/*/metrics.json'))[-1])); assert d['repos']=={}; print('OK')"
```
Expected: `OK`.

- [ ] **Step 6: Confirm full test suite still green**

```bash
python3 -m pytest tests/ -v
```
Expected: 20 passed.

- [ ] **Step 7: Commit**

```bash
git add gh_contributions/run.py README.md
git commit -m "feat: run entry point and README"
git log --oneline -8
```

---

## Success Criteria (final verification)

Run all six checks; each must pass:

```bash
python3 -m pytest tests/ -q
python3 -c "from gh_contributions import config, github_client, fetch, metrics, run; print('imports OK')"
grep -q '^out/$' .gitignore && echo ".gitignore has out/: OK"
env -u GITHUB_TOKEN python3 -m gh_contributions.run 2>&1 | grep -q GITHUB_TOKEN && echo "missing-token error: OK"
python3 -m gh_contributions.run /nope.yml; [ $? -eq 2 ] && echo "bad-config exit 2: OK"
[ -z "$(git status --porcelain)" ] && echo "working tree clean: OK"
```

Expected: `20 passed`, `imports OK`, `.gitignore has out/: OK`, `missing-token error: OK`, `bad-config exit 2: OK`, `working tree clean: OK`.
