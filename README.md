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
# Analysis window starts on this date (UTC) and ends on the last day of the
# previous calendar month. The current month is excluded so we don't cache
# partial data.
since: 2026-01-01
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

Output: `out/<UTC-timestamp>/metrics.json`. Raw API responses are cached under `out/raw/<YYYY-MM>/<owner>__<repo>/` and reused across runs — see `## Raw-data cache` below.

## Raw-data cache

Raw API responses are stored under `out/raw/<YYYY-MM>/<owner>__<repo>/`, one bucket
per (month, repo). Runs enumerate months from `since` to today (UTC) and only
fetch buckets that are not already complete on disk. A bucket is complete when
its `_meta.json` exists and contains no `error` key.

To force a refresh of a specific bucket, delete it and re-run:

```bash
rm -rf out/raw/2026-07/acme__api
python3 -m gh_contributions.run
```

The current calendar month is never fetched or cached — the analysis window ends
on the last day of the previous month (UTC). This keeps every cached bucket a
complete, immutable month. To see activity for the current month, wait until it
ends.

## Report

Turn the run's `metrics.json` into a self-contained HTML page (Chart.js inlined, works offline):

```bash
python3 -m gh_contributions.report            # newest out/*/
python3 -m gh_contributions.report out/2026-07-01T201112Z
```

Output: `out/<run>/report.html`. Open it in a browser. First tab (`All repos`) aggregates across every configured repo; the rest are one tab per repo.

## Test

```bash
python3 -m pytest -q
```
