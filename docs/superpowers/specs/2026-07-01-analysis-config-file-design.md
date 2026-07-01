# Analysis Config File — Design

**Date:** 2026-07-01
**Status:** Approved
**Scope:** Rename `usernames.yml` to `config.yml`, extend it to describe the full analysis run (users, repos, date window, metrics), and update `.gitignore`.

## Goal

Replace the single-purpose `usernames.yml` with a `config.yml` that carries everything the (future) analyzer needs to know: which people to measure, which repositories to scan, over what date window, and which metrics to compute.

The file stays local-only (git-ignored); no analyzer code is written as part of this change.

## Schema

Flat top-level keys. Chosen for readability while the config is small; sections can be nested later if the file grows.

| Key | Type | Required | Meaning |
|---|---|---|---|
| `usernames` | list of strings | yes, non-empty | GitHub logins whose contributions are analyzed. |
| `repos` | list of strings, each `owner/repo` | yes; may be empty | Repositories to scan. Empty means "none configured yet" — analyzer warns, does not crash. |
| `since` | date, `YYYY-MM-DD` | yes | Inclusive lower bound of the analysis window. |
| `until` | date, `YYYY-MM-DD` | yes | Inclusive upper bound. Must be >= `since`. |
| `metrics` | list of strings | yes, non-empty | Which stats to compute. Allowed values: `commits`, `pull_requests`, `reviews`, `issues`, `comments`. Unknown values are rejected by the analyzer. |

Validation rules (for the future analyzer, captured here so behavior is fixed):

- Empty `usernames` → error.
- Empty `metrics` → error.
- Empty `repos` → warning only.
- `until < since` → error.
- Metric value outside the allowed set → error naming the offending value.
- Repo entry not matching `owner/repo` → error naming the offending entry.

## Example

```yaml
# Configuration for GitHub contribution analysis. Do not commit.

usernames:
  - ceclan-bianca
  - balajcosmin-ppb
  # ... remaining users preserved verbatim from usernames.yml ...
  - carinac-sportsbet

repos: []   # e.g. - myorg/api-service

since: 2025-01-01
until: 2025-12-31

metrics:
  - commits
  - pull_requests
  - reviews
```

## Concrete Changes

1. Rename `usernames.yml` → `config.yml` (plain `mv` — the file is git-ignored and untracked, so `git mv` does not apply).
2. Rewrite the file body to match the schema:
   - Keep all 21 existing usernames under the `usernames` key.
   - Add `repos: []` with a comment showing the expected entry format.
   - Add `since: 2025-01-01` and `until: 2025-12-31` as placeholder defaults (user edits later).
   - Add `metrics` seeded with `commits`, `pull_requests`, `reviews`.
   - Update the top-of-file comment to say "Configuration for GitHub contribution analysis. Do not commit." rather than referring only to usernames.
3. Update `.gitignore`: replace the `usernames.yml` entry with `config.yml`.

## Out of Scope

- Any analyzer implementation (fetching data, computing stats, output).
- Per-repo overrides (branches, path filters, include-forks). Reachable later by promoting each repo entry from a string to an object; not needed now.
- Output format setting. Add when there is a runner that produces output.
- Auth/token configuration. Belongs in environment, not this file.

## Success Criteria

- `config.yml` exists at repo root with the schema above and all 21 usernames preserved.
- `usernames.yml` no longer exists.
- `.gitignore` ignores `config.yml` and no longer references `usernames.yml`.
- `git status` shows `config.yml` as ignored (untracked but hidden).
