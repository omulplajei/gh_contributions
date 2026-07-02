# Shell wrappers for fetch and report — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `./fetch.sh` and `./report.sh` at repo root that wrap the existing Python entry points and load `GITHUB_TOKEN` from `.env`, and update the README to document them.

**Architecture:** Two thin bash wrappers at repo root. `fetch.sh` sources `.env` (fails loudly if missing or if `GITHUB_TOKEN` is empty) and `exec`s `python3 -m gh_contributions.run "$@"`. `report.sh` just `exec`s `python3 -m gh_contributions.report "$@"` — the report needs no token. Both `cd` to their own directory first so they work from any cwd.

**Tech Stack:** bash, existing `gh_contributions` Python package.

**Spec:** [docs/superpowers/specs/2026-07-02-shell-wrappers-design.md](../specs/2026-07-02-shell-wrappers-design.md)

## Global Constraints

- Shebang: `#!/usr/bin/env bash`.
- Both scripts start with `set -euo pipefail`.
- Both scripts start with `cd "$(dirname "$0")"` so they resolve `.env` / `config.yml` / `out/` relative to their own location (repo root).
- Both scripts end with `exec python3 -m gh_contributions.<module> "$@"` so exit codes and signals propagate and args are forwarded.
- Exit code `2` for wrapper-level errors (missing `.env`, empty `GITHUB_TOKEN`), matching the Python entry point's convention for config/env errors.
- Files checked in with the executable bit set (`chmod +x`).
- Do not modify any Python source, tests, or `.gitignore`. The `python3 -m …` invocations remain supported.

---

## File Structure

- Create: `fetch.sh` — wrapper that sources `.env` and invokes `gh_contributions.run`.
- Create: `report.sh` — wrapper that invokes `gh_contributions.report`.
- Modify: `README.md` — replace `## Run` and `## Report` sections; touch the `## Raw-data cache` example.

No source code, no tests, no config files touched.

---

## Task 1: `fetch.sh`

**Files:**
- Create: `fetch.sh`

**Interfaces:**
- Consumes: `.env` at repo root defining `export GITHUB_TOKEN=…`; `gh_contributions.run` (already exists, accepts optional config path as first positional arg).
- Produces: an executable `./fetch.sh` that other tasks (README) reference by name.

- [ ] **Step 1: Create `fetch.sh` with exact content below**

File: `fetch.sh`

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

- [ ] **Step 2: Make it executable**

Run:
```bash
chmod +x fetch.sh
```

- [ ] **Step 3: Verify the missing-`.env` error path**

Run:
```bash
mv .env .env.bak
./fetch.sh; echo "exit=$?"
mv .env.bak .env
```

Expected stderr contains:
```
fetch.sh: .env not found at <repo-root>/.env; create it with:
  export GITHUB_TOKEN=<personal access token with repo:read>
```
And prints `exit=2`.

- [ ] **Step 4: Verify the empty-token error path**

Run:
```bash
cp .env .env.bak
printf 'export GITHUB_TOKEN=\n' > .env
./fetch.sh; echo "exit=$?"
mv .env.bak .env
```

Expected stderr contains:
```
fetch.sh: GITHUB_TOKEN not set after sourcing .env
```
And prints `exit=2`.

- [ ] **Step 5: Verify happy-path arg forwarding without hitting the network**

Run:
```bash
./fetch.sh tests/fixtures/authoring/config.yml; echo "exit=$?"
```

Expected: exits `0` (the fixture config resolves to zero months to fetch or all-cached buckets, so no network call is required). Stderr shows the usual `run dir: …` / `raw cache: …` lines from `gh_contributions.run`.

If this fixture happens to require network in this checkout, substitute any config with `since` inside the current month, which also short-circuits `gh_contributions.run` before any GitHub call.

- [ ] **Step 6: Commit**

Run:
```bash
git add fetch.sh
git update-index --chmod=+x fetch.sh
git commit -m "feat: add fetch.sh wrapper that sources .env"
```

---

## Task 2: `report.sh`

**Files:**
- Create: `report.sh`

**Interfaces:**
- Consumes: `gh_contributions.report` (already exists, accepts optional run-dir path as first positional arg).
- Produces: an executable `./report.sh` that the README references by name.

- [ ] **Step 1: Create `report.sh` with exact content below**

File: `report.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

exec python3 -m gh_contributions.report "$@"
```

- [ ] **Step 2: Make it executable**

Run:
```bash
chmod +x report.sh
```

- [ ] **Step 3: Verify the "no run dirs" error path propagates**

Run (in a scratch dir with no `out/` — simulate by pointing at a temp dir):
```bash
tmpdir=$(mktemp -d)
cp report.sh "$tmpdir/"
(cd "$tmpdir" && ./report.sh); echo "exit=$?"
rm -rf "$tmpdir"
```

Expected stderr:
```
no run directories found under out/
```
And prints `exit=2`.

Note: this only works because the copied script `cd`s to its own dir (the tmp dir), which has no `out/`. If step 3 in Task 1 was skipped and `cd "$(dirname "$0")"` was omitted, this test would fail.

- [ ] **Step 4: Verify happy-path against an existing run (skip if `out/` has no runs)**

Run:
```bash
ls out/ 2>/dev/null | grep -v '^raw$' | head -1
```

If output is non-empty, run:
```bash
./report.sh; echo "exit=$?"
```

Expected: exit `0`, stderr shows `wrote out/<run>/report.html`.

If `out/` has no non-`raw` subdirs, skip this step — Task 1's happy-path test may have produced one; otherwise this step is optional.

- [ ] **Step 5: Commit**

Run:
```bash
git add report.sh
git update-index --chmod=+x report.sh
git commit -m "feat: add report.sh wrapper"
```

---

## Task 3: README update

**Files:**
- Modify: `README.md` (`## Run` → `## Fetch`, `## Report` body, one line inside `## Raw-data cache`).

**Interfaces:**
- Consumes: `./fetch.sh`, `./report.sh` from Tasks 1 and 2.
- Produces: user-visible docs. No other task depends on this.

- [ ] **Step 1: Replace the `## Run` section**

Current (in [README.md](../../../README.md), the section starting at `## Run`):

````markdown
## Run

```bash
export GITHUB_TOKEN=<personal access token with repo:read>
python3 -m gh_contributions.run
```

Output: `out/<UTC-timestamp>/metrics.json`. Raw API responses are cached under `out/raw/<YYYY-MM>/<owner>__<repo>/` and reused across runs — see `## Raw-data cache` below.
````

Replace with:

````markdown
## Fetch

Create `.env` at repo root (git-ignored — never commit):

```bash
echo 'export GITHUB_TOKEN=<personal access token with repo:read>' > .env
```

Then run:

```bash
./fetch.sh                          # uses config.yml
./fetch.sh path/to/other-config.yml # override config
```

Output: `out/<UTC-timestamp>/metrics.json`. Raw API responses are cached under `out/raw/<YYYY-MM>/<owner>__<repo>/` and reused across runs — see `## Raw-data cache` below.
````

- [ ] **Step 2: Update the `## Raw-data cache` refresh example**

Current block inside `## Raw-data cache`:

````markdown
```bash
rm -rf out/raw/2026-07/acme__api
python3 -m gh_contributions.run
```
````

Replace with:

````markdown
```bash
rm -rf out/raw/2026-07/acme__api
./fetch.sh
```
````

- [ ] **Step 3: Replace the `## Report` code block**

Current:

````markdown
## Report

Turn the run's `metrics.json` into a self-contained HTML page (Chart.js inlined, works offline):

```bash
python3 -m gh_contributions.report            # newest out/*/
python3 -m gh_contributions.report out/2026-07-01T201112Z
```

Output: `out/<run>/report.html`. Open it in a browser. First tab (`All repos`) aggregates across every configured repo; the rest are one tab per repo.
````

Replace with:

````markdown
## Report

Turn the run's `metrics.json` into a self-contained HTML page (Chart.js inlined, works offline):

```bash
./report.sh                          # newest out/*/
./report.sh out/2026-07-01T201112Z   # specific run
```

Output: `out/<run>/report.html`. Open it in a browser. First tab (`All repos`) aggregates across every configured repo; the rest are one tab per repo.
````

- [ ] **Step 4: Verify README renders as intended**

Run:
```bash
grep -n '^## ' README.md
```

Expected section order:
```
## Configure
## Install
## Fetch
## Raw-data cache
## Report
## Test
```

And confirm no residual `python3 -m gh_contributions.run` / `python3 -m gh_contributions.report` remain in the three updated blocks:

```bash
grep -n 'python3 -m gh_contributions\.\(run\|report\)' README.md || echo "none — good"
```

Expected: `none — good`. (The `python3 -m …` invocations are still supported by the code; the README just no longer advertises them as the primary entry point.)

- [ ] **Step 5: Commit**

Run:
```bash
git add README.md
git commit -m "docs(readme): document fetch.sh and report.sh"
```

---

## Self-Review Checklist

1. **Spec coverage**
   - `fetch.sh` design → Task 1. ✓
   - `report.sh` design → Task 2. ✓
   - README `## Run` → `## Fetch` rename → Task 3 Step 1. ✓
   - README `## Report` body replacement → Task 3 Step 3. ✓
   - README `## Raw-data cache` example update → Task 3 Step 2. ✓
   - Executable bit → Task 1 Step 2 + `git update-index --chmod=+x`, Task 2 Step 2 + `git update-index --chmod=+x`. ✓
   - Exit code 2 for wrapper errors → Task 1 Steps 3 & 4 verify. ✓
   - Manual smoke tests from the spec's "Testing" section → covered across Task 1 Steps 3–5 and Task 2 Steps 3–4. ✓

2. **Placeholder scan:** No TBD/TODO. All shell code is complete. All expected outputs are concrete.

3. **Type consistency:** Both scripts consistently use `python3 -m gh_contributions.<module>`, `$(dirname "$0")`, `exit 2`. README references match the filenames created in Tasks 1 and 2 (`./fetch.sh`, `./report.sh`).
