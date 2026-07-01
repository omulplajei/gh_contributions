# Analysis Config File Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `usernames.yml` with a git-ignored `config.yml` that describes the full analysis run (users, repos, date window, metrics), and update `.gitignore` accordingly.

**Architecture:** Pure filesystem change. No runtime code, no dependencies. Two commits: one for `.gitignore` (tracked), one is skipped for `config.yml` itself (git-ignored — verified by `git status --ignored`).

**Tech Stack:** YAML file, plain `mv`, standard `git`.

## Global Constraints

Copied verbatim from [design spec](../specs/2026-07-01-analysis-config-file-design.md):

- Flat top-level YAML keys — no nesting.
- Required keys: `usernames`, `repos`, `since`, `until`, `metrics`.
- `usernames`: non-empty list of strings.
- `repos`: list of strings, each matching `owner/repo` shape; may be empty.
- `since`, `until`: dates in `YYYY-MM-DD` format; `until >= since`.
- `metrics`: non-empty list; allowed values `commits`, `pull_requests`, `reviews`, `issues`, `comments`.
- File must be git-ignored (never committed).
- All 21 existing usernames from `usernames.yml` must be preserved verbatim in `config.yml`.

Validation rules for the future analyzer (recorded in the spec, not implemented here — this plan produces only the config file).

---

## Starting State

- `usernames.yml` exists at repo root, git-ignored, untracked.
- `.gitignore` has an **uncommitted** modification adding a `usernames.yml` entry (from the earlier setup step).
- `docs/superpowers/specs/2026-07-01-analysis-config-file-design.md` has been committed (commit `2a74cf3`).

Verify starting state:

```bash
git status --ignored
```

Expected output includes:
- `Changes not staged for commit: modified: .gitignore`
- `Ignored files: usernames.yml`

If that doesn't match, stop and reconcile before proceeding.

---

### Task 1: Update `.gitignore` to ignore `config.yml` instead of `usernames.yml`

**Files:**
- Modify: `.gitignore` (the last two lines added by the earlier setup)

**Interfaces:**
- Consumes: nothing.
- Produces: a committed `.gitignore` that ignores `config.yml`. Task 2 relies on this so the renamed file is ignored the moment it appears.

- [ ] **Step 1: Inspect the current tail of `.gitignore`**

Run:
```bash
tail -n 5 .gitignore
```

Expected (last 5 lines):
```
replay_pid*

# Local list of GitHub usernames to analyze
usernames.yml
```

If the trailing block differs, stop and reconcile — the plan assumes this exact state.

- [ ] **Step 2: Replace the trailing block**

Open `.gitignore` and change the last two non-blank lines from:

```
# Local list of GitHub usernames to analyze
usernames.yml
```

to:

```
# Local analysis config (users, repos, date window, metrics). Do not commit.
config.yml
```

- [ ] **Step 3: Verify the edit**

Run:
```bash
tail -n 5 .gitignore
```

Expected (last 5 lines):
```
replay_pid*

# Local analysis config (users, repos, date window, metrics). Do not commit.
config.yml
```

Also confirm no lingering reference to the old name:
```bash
grep -n usernames.yml .gitignore
```
Expected: no output (exit code 1).

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: ignore config.yml (replaces usernames.yml entry)"
```

Verify:
```bash
git log -1 --oneline
git status
```

Expected: the new commit is on top, and `git status` shows a clean working tree except for the still-ignored `usernames.yml` (which Task 2 renames away).

---

### Task 2: Rename `usernames.yml` → `config.yml` and rewrite its contents

**Files:**
- Delete: `usernames.yml`
- Create: `config.yml` (git-ignored)

**Interfaces:**
- Consumes: `.gitignore` from Task 1 (so `config.yml` is ignored on creation).
- Produces: `config.yml` conforming to the spec schema, containing all 21 existing usernames.

- [ ] **Step 1: Capture the current usernames**

Run:
```bash
cat usernames.yml
```

Expected: the file lists 21 usernames under a `usernames:` key. Save this output mentally / on-screen — the same 21 names must appear verbatim in the new file.

- [ ] **Step 2: Rename the file**

Use plain `mv` (the file is untracked/ignored, so `git mv` does not apply):

```bash
mv usernames.yml config.yml
```

Verify:
```bash
test ! -e usernames.yml && test -f config.yml && echo OK
```
Expected: `OK`.

- [ ] **Step 3: Rewrite `config.yml` to match the schema**

Overwrite `config.yml` with the following exact content (preserving all 21 usernames verbatim from the previous file):

```yaml
# Configuration for GitHub contribution analysis. Do not commit.

usernames:
  - ceclan-bianca
  - balajcosmin-ppb
  - salcedobellaa
  - dancatalinbagacian
  - AndradaHruban1
  - Vpuscas13
  - crisanraul
  - Banyaszs
  - vivienstratulat-flutter
  - breazc
  - iliesg-ppb
  - mihaitataruPPB
  - mihai-hanga
  - omulplajei
  - LauraMironMihaela
  - curmeid1
  - savoiua
  - vpuia
  - robert-stancu
  - mihai-mocanu
  - carinac-sportsbet

repos: []   # e.g. - myorg/api-service

since: 2025-01-01
until: 2025-12-31

metrics:
  - commits
  - pull_requests
  - reviews
```

- [ ] **Step 4: Verify contents**

Run:
```bash
grep -c '^  - ' config.yml
```
Expected: `24` (21 usernames + 3 metrics).

Run:
```bash
grep -E '^(usernames|repos|since|until|metrics):' config.yml
```
Expected output (order preserved):
```
usernames:
repos: []   # e.g. - myorg/api-service
since: 2025-01-01
until: 2025-12-31
metrics:
```

Run a YAML parse sanity check (Python ships with macOS):
```bash
python3 -c "import yaml,sys; d=yaml.safe_load(open('config.yml')); assert set(d)=={'usernames','repos','since','until','metrics'}; assert len(d['usernames'])==21; assert d['repos']==[]; print('OK')"
```
Expected: `OK`.

If `python3 -c "import yaml"` fails (PyYAML not installed), fall back to:
```bash
python3 -c "import sys; ls=open('config.yml').read().splitlines(); assert '- ceclan-bianca' in [l.strip() for l in ls]; assert '- carinac-sportsbet' in [l.strip() for l in ls]; print('OK')"
```
Expected: `OK`.

- [ ] **Step 5: Verify git-ignored, not tracked**

```bash
git status --ignored
```
Expected:
- Working tree clean under "Changes not staged" / "Untracked".
- `config.yml` listed under `Ignored files`.
- No mention of `usernames.yml`.

- [ ] **Step 6: No commit for this task**

`config.yml` is intentionally not tracked. Do **not** run `git add -f`. This task ends without a git commit; Task 1's commit is the only one produced by this plan.

Sanity:
```bash
git log --oneline -3
```
Expected: the top commit is Task 1's `chore: ignore config.yml ...`, then the design-spec commit, then `Initial commit`.

---

## Success Criteria (final verification)

Run all four checks; each must pass:

```bash
test ! -e usernames.yml && echo "usernames.yml gone: OK"
test -f config.yml && echo "config.yml present: OK"
git check-ignore -v config.yml && echo "config.yml ignored: OK"
! grep -q usernames.yml .gitignore && echo ".gitignore clean of old name: OK"
```

Expected: four `OK` lines.
