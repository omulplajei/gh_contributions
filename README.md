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
