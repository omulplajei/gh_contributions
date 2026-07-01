# Team Activity Metrics — Design

**Date:** 2026-07-01
**Status:** Draft (pending user review)
**Scope:** Extend the analysis config with a broader, layered catalog of GitHub activity metrics for the developers listed in `usernames:`, plus a team-vs-overall share view per repo. No analyzer code is written as part of this change.

**Supersedes:** the `metrics:` whitelist in [2026-07-01-analysis-config-file-design.md](2026-07-01-analysis-config-file-design.md) (`commits`, `pull_requests`, `reviews`, `issues`, `comments`). This spec replaces those values.

## Goal

Give a per-developer activity snapshot across the repos configured in `config.yml`, over the `since`..`until` window, weighted toward:

1. **Raw output volume** — what each developer produced.
2. **Invisible / collaborative work** — reviews, comments, cross-team help.
3. **Team-vs-overall share** — the listed team's footprint compared to *all* contributors in each repo.

Presence/timing signals, PR-size and process/quality signals, and activity outside the configured repos are explicitly out of scope (see below).

## Approach

Layered catalog. Three explicit layers, each with a clear role. The layers are also the values accepted in `metrics:` — enabling a layer enables every metric under it.

## Metric Catalog

Every metric is computed **per developer, per configured repo, within `[since, until]`** unless otherwise noted.

### Layer 1 — Authoring

| Metric | Definition |
|---|---|
| `commits` | Commits authored by the user. All branches. Merge commits included. |
| `pull_requests_opened` | PRs authored by the user, `created_at` in window. |
| `pull_requests_merged` | PRs authored by the user, `merged_at` in window. |
| `issues_opened` | Issues authored by the user, `created_at` in window. |

### Layer 2 — Collaboration

| Metric | Definition |
|---|---|
| `reviews_given` | Reviews submitted by the user, broken down by state: `APPROVED`, `CHANGES_REQUESTED`, `COMMENTED`. Self-reviews counted. |
| `review_comments` | Inline PR review comments authored by the user. |
| `pr_conversation_comments` | Top-level PR comments (not review comments) authored by the user. |
| `issue_comments` | Comments on issues authored by the user. |
| `cross_team_reviews` | Subset of `reviews_given` where the PR author is **not** in `usernames:`. Signals help offered outside the team. |

### Layer 3 — Team-vs-overall share (per repo)

For each repo, the team's percentage of overall activity in the window. Denominator includes **all** contributors, including bots.

| Metric | Definition |
|---|---|
| `share_commits` | `commits by team members / commits by everyone`. |
| `share_pull_requests_opened` | Team's PRs opened / everyone's PRs opened. |
| `share_reviews_given` | Team's reviews / everyone's reviews. |
| `share_comments` | Team's total of (`review_comments` + `pr_conversation_comments` + `issue_comments`) divided by the same total across everyone. |

"Team" = every login listed under `usernames:`.

## Fixed decisions

Recorded to prevent relitigation:

- **Commit branch scope:** all branches (not default-branch-only).
- **Merge commits:** included in `commits`.
- **Bot handling:** bots (`dependabot[bot]`, `github-actions[bot]`, …) are counted in the "everyone" denominator raw — no filtering.
- **Self-reviews:** counted in `reviews_given`.
- **Timezone:** `since`/`until` interpreted as UTC day boundaries. GitHub timestamps are UTC; no conversion.

## Config Surface

`config.yml`'s `metrics:` key accepts **layer names**. Three valid values:

- `authoring`
- `collaboration`
- `team_share`

Each enables every metric in that layer. No per-metric toggles.

Updated example (replaces the example in the analysis-config-file spec):

```yaml
# Configuration for GitHub contribution analysis. Do not commit.

usernames:
  - ceclan-bianca
  # ... remaining users ...
  - carinac-sportsbet

repos: []   # e.g. - myorg/api-service

since: 2026-01-01
until: 2026-07-01

metrics:
  - authoring
  - collaboration
  - team_share
```

Validation rules (unchanged in shape from the analysis-config spec; only the allowed values change):

- Empty `metrics` → error.
- Value outside `{authoring, collaboration, team_share}` → error naming the offending value.
- No aliases for the old whitelist (`commits`, `pull_requests`, `reviews`, `issues`, `comments`). The prior spec is superseded.

## Data Sources (feasibility)

Each metric maps to an existing GitHub API endpoint. This section confirms reachability; the analyzer implementation is separate.

| Layer / metric | Source |
|---|---|
| `commits` | Search API: `search/commits?q=repo:{owner/repo}+author:{login}+committer-date:{since}..{until}`. Covers all branches. |
| `pull_requests_opened` | Search API: `search/issues?q=repo:{owner/repo}+is:pr+author:{login}+created:{since}..{until}`. |
| `pull_requests_merged` | Same, with `merged:{since}..{until}`. |
| `issues_opened` | Search API: `search/issues?q=repo:{owner/repo}+is:issue+author:{login}+created:{since}..{until}`. |
| `reviews_given` | `GET /repos/{owner/repo}/pulls/{n}/reviews` per PR touched in window, or GraphQL `pullRequest.reviews`. Filter by user + `submitted_at`. |
| `review_comments` | `GET /repos/{owner/repo}/pulls/comments` (repo-wide), filter by user + `created_at`. Paginated. |
| `pr_conversation_comments` | `GET /repos/{owner/repo}/issues/comments`, filter by user + `created_at`, keep only ones on PRs. |
| `issue_comments` | Same endpoint, keep only ones on issues (not PRs). |
| `cross_team_reviews` | Subset of `reviews_given` where PR `author.login ∉ usernames:`. No new source. |
| Layer 3 share metrics | Same sources as Layers 1 & 2. Aggregate over all contributors for the denominator; divide the team's sum by it. |

### Feasibility notes

- **Preferred access pattern (guidance, not implementation).** Fetch each source once per repo, bucket by author, then compute per-user *and* team-share denominators in a single pass. Cheaper than per-user queries and produces the share denominator for free.
- **Search API rate limit.** 30 requests/min authenticated. If a search query has >1000 hits, results cap at 1000 — a real risk for `commits` on busy repos over long windows. Mitigation (future analyzer): fall back to `GET /repos/{owner/repo}/commits` per branch when the cap is hit. Not designed here.
- **Cost hot spot.** `reviews_given` scales with the number of PRs touched in window (one call per PR unless GraphQL is used). Everything else is repo-wide list endpoints.

## Out of Scope

Deliberate "no"s so we don't relitigate:

- **Comments received on the user's PRs** — dropped this round for per-PR cost.
- **Presence / activity timing** — no `days_active`, `first_activity`, `last_activity`, no time-of-day patterns.
- **Lines added/removed, files touched, PR size** — game-able and redundant with `commits` and `pull_requests_*`.
- **Time-to-merge, time-to-first-review, review turnaround** — process/quality signals; not this purpose.
- **Deployment / release / CI signals.**
- **Discussions, gists, reactions, stars.**
- **User-scoped activity outside configured repos** — repo-scoped only.
- **Cross-team helping *received*** (non-team reviewers on team PRs). Cheap to add later; not now.
- **Per-metric fine-grained enable/disable** in `config.yml`. `exclude_metrics:` reserved for a future spec if a real need appears.
- **Auth / token config, output format** — as in the parent analysis-config spec, still out of scope.

## Concrete Changes This Spec Implies

To be executed by a later implementation plan, not by this spec:

1. Update `config.yml`'s `metrics:` value to the new layer-name whitelist (`authoring`, `collaboration`, `team_share`). Replace the example in the parent spec accordingly.
2. Update the validation rules embedded in the parent spec to reference the new whitelist.
3. No new files, no analyzer code, no dependency changes.

## Success Criteria

- `config.yml` uses `metrics:` values drawn only from `{authoring, collaboration, team_share}`.
- The parent analysis-config spec's whitelist and example reflect the new values (or link forward to this spec as the source of truth).
- The metric catalog in this document is complete: every metric has a definition and an identified data source.
- No mention of dropped/out-of-scope items in the live config or example.
