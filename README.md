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
