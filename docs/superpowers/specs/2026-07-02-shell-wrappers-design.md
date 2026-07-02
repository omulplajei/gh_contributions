# Shell wrappers for fetch and report

Convenience shell scripts at repo root that wrap the two existing Python entry
points and source `GITHUB_TOKEN` from `.env`.

## Motivation

Today the documented invocation is:

```bash
export GITHUB_TOKEN=<token>
python3 -m gh_contributions.run
python3 -m gh_contributions.report
```

Users already keep their token in `.env` at repo root (git-ignored, already in
shell-sourceable `export KEY=value` form). Two thin wrappers remove the manual
`export` step and the `python3 -m …` boilerplate for the common case.

## Scope

In scope:

- `fetch.sh` — wraps `python3 -m gh_contributions.run`, loads `.env` first.
- `report.sh` — wraps `python3 -m gh_contributions.report`. No token needed.
- README updates so the scripts are the documented entry points.

Out of scope:

- Virtualenv management, dependency install, Python auto-detection.
- Any change to the Python code itself.
- Windows / PowerShell equivalents.
- Removing or deprecating the `python3 -m …` invocations — they remain
  supported and unchanged.

## Design

### `fetch.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "fetch.sh: .env not found at $(pwd)/.env; create it with:" >&2
  echo "  export GITHUB_TOKEN=<personal access token with repo:read>" >&2
  exit 2
fi

# shellcheck disable=SC1091
source .env

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "fetch.sh: GITHUB_TOKEN not set after sourcing .env" >&2
  exit 2
fi

exec python3 -m gh_contributions.run "$@"
```

Behavior notes:

- `cd "$(dirname "$0")"` makes the script safe to invoke from any cwd; it
  always resolves `.env`, `config.yml`, and the `out/` directory relative to
  the repo root.
- `source .env` matches the current `.env` format (`export GITHUB_TOKEN=…`)
  exactly — no parsing, no `set -a` trickery.
- `exec` replaces the shell so the underlying Python's exit code propagates
  and signal handling stays clean.
- `"$@"` forwards extra args (e.g. `./fetch.sh path/to/other-config.yml`),
  which `gh_contributions.run` already accepts as an optional config path.

### `report.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

exec python3 -m gh_contributions.report "$@"
```

No `.env` load: `gh_contributions.report` only reads local files under
`out/` and never calls the GitHub API.

`"$@"` forwards an optional run directory, matching the current CLI
(`./report.sh out/2026-07-01T201112Z`).

### File permissions

Both scripts are checked in with the executable bit set (`chmod +x`) so users
can invoke them as `./fetch.sh` / `./report.sh` without a preceding `bash`.

## README changes

Restructure the current `## Run` and `## Report` sections:

1. Rename `## Run` to `## Fetch`. Replace its body with:

   Create `.env` at repo root (git-ignored — never commit):

   ```bash
   echo 'export GITHUB_TOKEN=<personal access token with repo:read>' > .env
   ```

   Then:

   ```bash
   ./fetch.sh                          # uses config.yml
   ./fetch.sh path/to/other-config.yml # override config
   ```

   Output: `out/<UTC-timestamp>/metrics.json`. Raw API responses are cached
   under `out/raw/<YYYY-MM>/<owner>__<repo>/` and reused across runs — see
   `## Raw-data cache` below.

2. Replace the `## Report` code block with:

   ```bash
   ./report.sh                          # newest out/*/
   ./report.sh out/2026-07-01T201112Z   # specific run
   ```

3. Leave `## Configure`, `## Install`, `## Raw-data cache`, and `## Test`
   unchanged. The existing `rm -rf out/raw/2026-07/acme__api && python3 -m
   gh_contributions.run` example in `## Raw-data cache` is updated to use
   `./fetch.sh` for consistency.

## Error handling

| Condition                              | Behavior                              | Exit code |
| -------------------------------------- | ------------------------------------- | --------- |
| `.env` missing (fetch.sh)              | Print error + fix hint to stderr      | 2         |
| `GITHUB_TOKEN` empty after sourcing    | Print error to stderr                 | 2         |
| Underlying Python entry point fails    | Propagated via `exec`                 | passthru  |
| Extra args (both scripts)              | Forwarded verbatim to Python          | passthru  |

Exit code `2` matches what `gh_contributions.run` already uses for
configuration / environment errors, so callers can treat the wrapper's
`2` interchangeably with the Python's `2`.

## Testing

No automated tests. The wrappers are ≤15 lines of straight-line shell with
no branching beyond an existence check and a non-empty check; the underlying
Python is already covered by `tests/`.

Manual smoke test (documented in the plan, not in the repo):

1. With a valid `.env`: `./fetch.sh` succeeds, produces `out/<ts>/metrics.json`.
2. Without `.env`: `./fetch.sh` prints the fix hint and exits 2.
3. With `.env` present but `GITHUB_TOKEN=` (empty): exits 2 with the
   "not set after sourcing" message.
4. `./report.sh` on the run from step 1 produces `report.html`.
5. `./report.sh some/nonexistent/dir` exits 2 (propagated from Python).

## Security

- `.env` stays git-ignored (unchanged from today).
- `source .env` runs whatever is in `.env` as shell, which is the same trust
  boundary users already accept when they created the file. No new risk.
- Scripts never echo the token.
