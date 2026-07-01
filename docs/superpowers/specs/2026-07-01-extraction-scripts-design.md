# GitHub Contribution Extraction Scripts — Design

**Date:** 2026-07-01
**Status:** Draft (pending user review)
**Scope:** Implement the scripts that read `config.yml`, fetch the raw GitHub data for the metrics catalog, and produce a single `metrics.json` per run. Metric semantics, endpoints, and validation rules are inherited unchanged from the prior specs.

**Depends on:**
- [2026-07-01-analysis-config-file-design.md](2026-07-01-analysis-config-file-design.md) — `config.yml` schema and validation rules.
- [2026-07-01-team-activity-metrics-design.md](2026-07-01-team-activity-metrics-design.md) — metric catalog, data sources, fixed decisions (bots, self-reviews, UTC, all-branches, merge commits).

## Goal

A runnable Python package that, given a valid `config.yml` and a `GITHUB_TOKEN`, produces `out/<run>/metrics.json` containing per-repo, per-user metrics for every layer enabled in `config.yml`.

## Non-Goals

- CI configuration.
- Reporting / rendering (Markdown or HTML views).
- A `compute`-only subcommand (structure permits it later; not built now).
- Search API 1000-hit fallback (spec-deferred).
- Concurrency across repos or endpoints (sequential is enough given rate limits are the bottleneck).

## Architecture

Python 3 package, three concerns kept behind explicit module boundaries so the metrics layer is a pure function of on-disk raw pages.

```
gh_contributions/
  __init__.py
  config.py         # load + validate config.yml
  github_client.py  # auth, pagination, rate-limit backoff, retry
  fetch.py          # per-repo fetchers, write raw JSON pages to disk
  metrics.py        # pure functions: raw pages -> metrics dict
  run.py            # __main__: config -> fetch -> compute -> write metrics.json
tests/
  test_config.py
  test_metrics.py
  fixtures/
    simple/
      config.yaml
      raw/<owner>__<name>/*.json
requirements.txt    # requests, PyYAML, pytest
```

**Data flow:** `config.yml` → `Config` → per-repo raw pages under `out/<run>/raw/` → `metrics.compute` reads that directory → `out/<run>/metrics.json`.

The disk boundary between fetch and compute is the design's load-bearing decision:
- Metrics are unit-testable without HTTP mocking — tests hand `metrics.compute` a fixture directory.
- Raw pages become an audit trail: any metric value can be traced back to the JSON page it came from.
- A future `compute` subcommand re-runs metrics without re-hitting the API.

## Module Contracts

### `config.py`

`load_config(path: str) -> Config` where `Config` is a dataclass with:
- `usernames: list[str]`
- `repos: list[str]` (each `owner/repo`)
- `since: datetime.date`
- `until: datetime.date`
- `metrics: list[str]` (subset of `{authoring, collaboration, team_share}`)

Validation (raises `ConfigError`; caller exits 2):

| Rule | Behavior |
|---|---|
| Empty `usernames` | error |
| Empty `metrics` | error |
| Metric value ∉ `{authoring, collaboration, team_share}` | error, message names value |
| `until < since` | error |
| Repo entry not matching `^[^/]+/[^/]+$` | error, message names entry |
| Empty `repos` | warning on stderr, no error |

No network. Pure YAML parsing + shape checks.

### `github_client.py`

```python
class GitHubClient:
    def __init__(self, token: str) -> None: ...
    def get_paginated(self, path: str, params: dict) -> Iterator[list[dict]]: ...
    def search_paginated(self, path: str, params: dict) -> Iterator[SearchPage]: ...
```

- `get_paginated` yields the parsed JSON of each REST page. Detects last page via missing `Link: rel="next"`.
- `search_paginated` yields `SearchPage(items: list[dict], total_count: int)` per page and stops at the API's 1000-item cap. `total_count` is preserved so callers know when truncation happened.
- Rate limiting: reads `X-RateLimit-Remaining`/`X-RateLimit-Reset`; sleeps until reset when remaining < 5.
- Secondary rate limit (429 with `Retry-After`) → sleep the header value, retry once.
- 5xx → sleep 2 s, retry once. Second failure → raise.
- 401 → raise immediately (bad token).
- 404 → raise `NotFoundError` (caller decides: skip the repo).

Zero knowledge of metrics; nothing here mentions users, PRs, or dates.

### `fetch.py`

`fetch_repo(client: GitHubClient, repo: str, since: date, until: date, out_dir: Path) -> None`

For one repo, calls each source **once** (per the metric spec's preferred access pattern) and writes:

```
out/<run>/raw/<owner>__<name>/
  commits.json              # search results (all authors)
  prs_by_created.json       # search: PRs created in window
  prs_by_merged.json        # search: PRs merged in window
  issues_by_created.json    # search: issues created in window
  prs_updated.json          # REST list: PRs updated in window (for reviews enumeration)
  reviews/<pr_number>.json  # one file per PR from prs_updated
  review_comments.json      # REST list, repo-wide
  issue_comments.json       # REST list, repo-wide (both PR + issue comments)
  _meta.json                # per-endpoint total_count and truncated flags
```

Each `*.json` (except `_meta.json`) is a JSON array — every page concatenated. `_meta.json` records:
```json
{
  "commits":            {"total_count": 4321, "truncated": true},
  "prs_by_created":     {"total_count": 200,  "truncated": false},
  ...
}
```

`fetch.py` performs pure I/O + shape. No filtering by user, no aggregation. Repos that return 404 or exhaust retries are recorded in `_meta.json` at the top level with `"error": "<reason>"` and no other files are written.

### `metrics.py`

`compute(raw_dir: Path, config: Config) -> dict` — pure function of the on-disk raw pages plus config. No HTTP. Reads only from `raw_dir`.

Output shape:

```json
{
  "run": {
    "since": "2026-01-01",
    "until": "2026-07-01",
    "generated_at": "2026-07-01T12:34:56Z",
    "metrics_layers": ["authoring", "collaboration", "team_share"]
  },
  "repos": {
    "owner/name": {
      "per_user": {
        "login": {
          "authoring": {
            "commits": 42,
            "pull_requests_opened": 7,
            "pull_requests_merged": 5,
            "issues_opened": 2
          },
          "collaboration": {
            "reviews_given": {"APPROVED": 12, "CHANGES_REQUESTED": 3, "COMMENTED": 8},
            "review_comments": 34,
            "pr_conversation_comments": 11,
            "issue_comments": 4,
            "cross_team_reviews": 6
          }
        }
      },
      "team_share": {
        "share_commits": 0.42,
        "share_pull_requests_opened": 0.55,
        "share_reviews_given": 0.31,
        "share_comments": 0.48
      },
      "truncated": {"commits": true},
      "error": null
    }
  }
}
```

Rules:
- Only layers in `config.metrics` appear in output. `per_user` is omitted when neither `authoring` nor `collaboration` is enabled. `team_share` block is omitted when `team_share` is not enabled.
- `per_user` keys are the exact logins from `config.usernames`. Bots and non-team users never appear as keys.
- Bots and non-team users still contribute to `team_share` denominators (spec-mandated).
- Share metrics: `null` when denominator is zero (JSON-safe, no division by zero).
- Reviews states counted: `APPROVED`, `CHANGES_REQUESTED`, `COMMENTED`. `DISMISSED` and `PENDING` ignored (spec explicit).
- Self-reviews (reviewer login ∈ `usernames` and equals PR author) counted (spec explicit).
- `cross_team_reviews` = total scalar count of the user's reviews (summed across the three counted states) where the PR author is not in `usernames`.
- `share_reviews_given` numerator and denominator are both totals across the three counted states — the per-state breakdown is a per-user detail only.
- `pr_conversation_comments` vs `issue_comments` split: a comment from `issue_comments.json` is a PR conversation comment iff its parent number appears in `prs_updated.json`, else it's an issue comment. Bounds cost to already-fetched data.
- `truncated` propagates any endpoint whose `_meta.json` says `truncated: true`.
- `error` propagates from `_meta.json`. Repos with a top-level fetch error have `per_user: null`, `team_share: null`, `truncated: null`, and `error: "<reason>"`. Successful repos have `error: null`.

### `run.py`

`python -m gh_contributions.run` — the single entry point.

1. Load config (exit 2 on `ConfigError`).
2. Read `GITHUB_TOKEN` from env (exit 2 if missing).
3. Create `out/<UTC-timestamp>/` (`YYYY-MM-DDTHHMMSSZ`).
4. For each repo: call `fetch_repo`. Log to stderr on progress. Errors in one repo don't abort others.
5. Call `metrics.compute(out_dir/"raw", config)`.
6. Write `out_dir/"metrics.json"`.
7. Exit 0 if any repo produced metrics, else 1. Config/auth errors are exit 2.

Empty `repos:` → warning, write `metrics.json` with `"repos": {}`, exit 0.

## Date Window Semantics

- `since` and `until` are UTC calendar dates from `config.yml`; both bounds are **inclusive** (spec-mandated: `until >= since`).
- Search API queries use GitHub's inclusive range syntax: `created:YYYY-MM-DD..YYYY-MM-DD`, `merged:...`, `committer-date:...`.
- For REST endpoints that don't accept a date filter (`pulls`, `pulls/comments`, `issues/comments`), we fetch with `sort=updated&direction=desc` and stop paginating once we cross below `since`. During metric computation, items are then filtered by the relevant timestamp field (`created_at` for comments, `submitted_at` for reviews, `merged_at` for merged PRs).
- Timestamp comparisons use `[since 00:00:00Z, until 23:59:59Z]` — the two calendar dates converted to UTC day boundaries.

## Error Handling & Edge Cases

| Situation | Behavior |
|---|---|
| Missing `GITHUB_TOKEN` | error to stderr, exit 2, no output written |
| Invalid config | error to stderr, exit 2, no output written |
| 401 from any endpoint | error to stderr, exit 2 |
| 404 on a repo | warn, record `error: "not_found"` in `_meta.json`, continue |
| Primary rate limit | sleep until `X-RateLimit-Reset` when remaining < 5 |
| Secondary rate limit (429) | honor `Retry-After`, retry once |
| 5xx | 2 s pause, retry once; second failure → skip repo, continue |
| Ctrl-C mid-fetch | partial `raw/` left; no `metrics.json`; re-run creates fresh dir |
| Empty repo results | empty JSON array written; downstream computes 0 counts / null shares |
| Search API cap hit | warn on stderr, `truncated: true` in `_meta.json`, propagates into result |

Exit codes: `0` any success, `1` all-repo failure, `2` config/auth error.

## Testing

`pytest`, no network, no HTTP mocking. Fixtures are hand-crafted JSON files under `tests/fixtures/`.

**`test_config.py`** — one test per validation rule (empty usernames, empty metrics, unknown metric, `until < since`, malformed repo entry, empty repos warns-not-errors, happy path).

**`test_metrics.py`** — hand-crafted `raw/<repo>/` directories cover:

- **Authoring:** commits split team vs non-team; PRs by created and merged both counted; bot excluded from `per_user`.
- **Collaboration:** review-state bucketing including ignored `DISMISSED`/`PENDING`; self-review counted; `cross_team_reviews` correctly derived; out-of-window comments excluded; PR-vs-issue comment split via known-PR-number rule.
- **Team share:** happy-path fraction; zero-denominator → `null`; bot in denominator; `share_comments` aggregates three sources; `truncated: true` propagates.
- **Layer selection:** enabling only `authoring` produces no `collaboration`/`team_share` output; enabling only `team_share` produces no `per_user`.

Not covered (validated by real runs): `github_client`, `fetch.py`, `run.py`.

## Concrete Changes This Spec Implies

To be executed by a later implementation plan, not this spec:

1. Create `gh_contributions/` package with the five modules above.
2. Create `tests/` with `test_config.py`, `test_metrics.py`, and `fixtures/`.
3. Create `requirements.txt` with `requests`, `PyYAML`, `pytest`.
4. Update `.gitignore` to ignore `out/`.
5. Update `README.md` with a usage snippet (`export GITHUB_TOKEN=...; python -m gh_contributions.run`).

## Success Criteria

- `python -m gh_contributions.run` with a valid `config.yml` and `GITHUB_TOKEN` writes `out/<timestamp>/metrics.json` with the shape above.
- Only layers listed in `config.metrics` appear in the output.
- `pytest` passes with no network access.
- Each raw API page is fetched at most once per run per repo.
- A 404 on one repo does not prevent the other repos from being processed.
- Search API truncation surfaces as `truncated: true` in `metrics.json`.
