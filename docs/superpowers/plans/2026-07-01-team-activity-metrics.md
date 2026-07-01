# Team Activity Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt the new layered metric whitelist in `config.yml` and mark the parent analysis-config spec as superseded on the `metrics:` field, so the design in [2026-07-01-team-activity-metrics-design.md](../specs/2026-07-01-team-activity-metrics-design.md) is the single source of truth for what `metrics:` accepts.

**Architecture:** Documentation + local config change only. No runtime code, no dependencies. One tracked commit (parent spec pointer) plus one untracked edit to the git-ignored `config.yml`.

**Tech Stack:** Markdown, YAML, plain `sed`/text edit, standard `git`.

## Global Constraints

Copied verbatim from the [team-activity-metrics design spec](../specs/2026-07-01-team-activity-metrics-design.md):

- `metrics:` in `config.yml` accepts **only** these three layer names: `authoring`, `collaboration`, `team_share`.
- No aliases for the previous whitelist (`commits`, `pull_requests`, `reviews`, `issues`, `comments`) — those values are no longer valid.
- `metrics:` must be non-empty.
- `config.yml` remains git-ignored — never committed, never `git add -f`'d.
- Parent spec `2026-07-01-analysis-config-file-design.md` is historical; do not rewrite its body. Add a forward pointer only.
- All other keys in `config.yml` (`usernames`, `repos`, `since`, `until`) MUST be preserved unchanged.

---

## Starting State

- Working tree clean. HEAD is `6a7eb18 docs: add team activity metrics design (supersedes prior metrics whitelist)`.
- `config.yml` exists, is git-ignored, and currently has `metrics: [commits, pull_requests, reviews]`.
- `docs/superpowers/specs/2026-07-01-analysis-config-file-design.md` is committed and unchanged since `2a74cf3`.

Verify starting state:

```bash
git status --ignored
git log --oneline -1
grep -E '^  - (commits|pull_requests|reviews)$' config.yml | wc -l | tr -d ' '
grep -c 'Superseded by' docs/superpowers/specs/2026-07-01-analysis-config-file-design.md
```

Expected:
- `git status --ignored` shows a clean tree and `config.yml` under Ignored files.
- HEAD is commit `6a7eb18`.
- The grep count on `config.yml` is `3` (the three old metric entries).
- The `Superseded by` grep count on the parent spec is `0`.

If any of these don't match, stop and reconcile before proceeding.

---

### Task 1: Add forward-pointing "Superseded by" notice to the parent analysis-config spec

**Files:**
- Modify: `docs/superpowers/specs/2026-07-01-analysis-config-file-design.md`

**Interfaces:**
- Consumes: nothing.
- Produces: a committed parent spec that carries a visible pointer to the new spec at both the top matter and the `metrics` schema row, so any reader knows the allowed `metrics:` values now live in the new spec.

- [ ] **Step 1: Inspect the current top matter of the parent spec**

Run:
```bash
sed -n '1,10p' docs/superpowers/specs/2026-07-01-analysis-config-file-design.md
```

Expected output (first 10 lines):
```
# Analysis Config File — Design

**Date:** 2026-07-01
**Status:** Approved
**Scope:** Rename `usernames.yml` to `config.yml`, extend it to describe the full analysis run (users, repos, date window, metrics), and update `.gitignore`.

## Goal

Replace the single-purpose `usernames.yml` with a `config.yml` that carries everything the (future) analyzer needs to know: which people to measure, which repositories to scan, over what date window, and which metrics to compute.

```

If it differs, stop and reconcile — Steps 2 and 3 assume this exact text.

- [ ] **Step 2: Insert a top-of-file supersede notice**

Replace the line:
```
**Status:** Approved
```
with:
```
**Status:** Approved (partially superseded — see note)
**Superseded by:** [2026-07-01-team-activity-metrics-design.md](2026-07-01-team-activity-metrics-design.md) for the `metrics:` whitelist and its validation. All other sections of this spec (`usernames`, `repos`, `since`, `until`, gitignore behavior) remain authoritative.
```

- [ ] **Step 3: Inspect the `metrics` schema row and validation rules**

Run:
```bash
grep -n '`metrics`' docs/superpowers/specs/2026-07-01-analysis-config-file-design.md
grep -n 'Metric value outside the allowed set' docs/superpowers/specs/2026-07-01-analysis-config-file-design.md
```

Expected: two hits — one row in the schema table describing `metrics`, and one line in the validation-rules list about disallowed values.

- [ ] **Step 4: Append a "Superseded — see" pointer to the `metrics` row**

Replace:
```
| `metrics` | list of strings | yes, non-empty | Which stats to compute. Allowed values: `commits`, `pull_requests`, `reviews`, `issues`, `comments`. Unknown values are rejected by the analyzer. |
```
with:
```
| `metrics` | list of strings | yes, non-empty | Which stats to compute. **Superseded — see** [team-activity-metrics design](2026-07-01-team-activity-metrics-design.md). Allowed values are now `authoring`, `collaboration`, `team_share`. The old values (`commits`, `pull_requests`, `reviews`, `issues`, `comments`) are no longer accepted. |
```

- [ ] **Step 4b: Update the Example YAML and the Concrete Changes seeding bullet**

The same spec has two more places that still show the old whitelist as active values: the `## Example` fenced YAML block, and the `## Concrete Changes` bullet that seeds `metrics`. Fix both so no reader lands on an unqualified stale value.

In the `## Example` fenced YAML block, locate the `metrics:` list and replace the three entries `- commits`, `- pull_requests`, `- reviews` with, verbatim:
```yaml
metrics:
  - authoring
  - collaboration
  - team_share
```
Preserve every other line in the example (comment header, `usernames:`, `repos:`, `since:`, `until:`) exactly as-is.

In the `## Concrete Changes` section, locate the nested bullet under item 2 that reads "Add `metrics` seeded with `commits`, `pull_requests`, `reviews`." and replace it with:
```
   - Add `metrics` seeded with `authoring`, `collaboration`, `team_share` (**superseded** — see [team-activity-metrics design](2026-07-01-team-activity-metrics-design.md); the original seeding used `commits`, `pull_requests`, `reviews`).
```
Do not touch other bullets in that list.

- [ ] **Step 5: Verify the edits**

Run:
```bash
grep -n 'Superseded by' docs/superpowers/specs/2026-07-01-analysis-config-file-design.md
grep -n 'authoring`, `collaboration`, `team_share' docs/superpowers/specs/2026-07-01-analysis-config-file-design.md
grep -c 'Allowed values: `commits`, `pull_requests`, `reviews`, `issues`, `comments`' docs/superpowers/specs/2026-07-01-analysis-config-file-design.md
grep -cE '^  - (commits|pull_requests|reviews)$' docs/superpowers/specs/2026-07-01-analysis-config-file-design.md
```

Expected:
- One hit for `Superseded by` (top-of-file notice).
- One hit for the new whitelist string.
- Zero hits for the old inline whitelist (it was replaced in the schema row).
- Zero hits for the old bare-list `- commits` / `- pull_requests` / `- reviews` entries (the Example YAML no longer seeds the old whitelist).

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/specs/2026-07-01-analysis-config-file-design.md
git commit -m "docs: mark analysis-config spec metrics field as superseded"
```

Verify:
```bash
git log --oneline -2
git status
```

Expected: the new commit is on top of `6a7eb18`, and `git status` shows a clean working tree except for the still-ignored `config.yml`.

---

### Task 2: Update `config.yml` `metrics:` to the layered whitelist

**Files:**
- Modify: `config.yml` (git-ignored — no commit)

**Interfaces:**
- Consumes: the layered whitelist established in Task 1's forward-pointer target.
- Produces: a local `config.yml` whose `metrics:` block contains exactly `authoring`, `collaboration`, `team_share`, with every other key preserved untouched.

- [ ] **Step 1: Snapshot the current file for safety**

Run:
```bash
cp config.yml config.yml.bak
```

The `.bak` file inherits the ignore rule for `config.yml`'s pattern? It does NOT — `.gitignore` matches `config.yml` exactly. Verify it stays untracked:
```bash
git check-ignore -v config.yml.bak || echo "not ignored (expected)"
git status --short config.yml.bak
```

Expected: `git check-ignore` prints `not ignored (expected)`; `git status --short` shows the file as untracked (`?? config.yml.bak`). Since the file is untracked and we will delete it in Step 5, this is fine. Do **not** `git add` it.

- [ ] **Step 2: Inspect the current `metrics:` block**

Run:
```bash
awk '/^metrics:/{flag=1} flag' config.yml
```

Expected output:
```
metrics:
  - commits
  - pull_requests
  - reviews
```

If it differs (extra metrics, different names), stop and reconcile before proceeding.

- [ ] **Step 3: Rewrite the `metrics:` block**

Overwrite the `metrics:` block only, preserving everything above it. Run this exact command:

```bash
python3 - <<'PY'
from pathlib import Path
p = Path("config.yml")
src = p.read_text()
marker = "metrics:\n"
idx = src.index(marker)
head = src[:idx]
new_tail = "metrics:\n  - authoring\n  - collaboration\n  - team_share\n"
p.write_text(head + new_tail)
PY
```

This preserves every line before `metrics:` verbatim (usernames, repos, since, until, comments, blank lines) and replaces everything from `metrics:` to end-of-file with the three layer entries.

- [ ] **Step 4: Verify the new file**

Run:
```bash
awk '/^metrics:/{flag=1} flag' config.yml
```

Expected output:
```
metrics:
  - authoring
  - collaboration
  - team_share
```

Verify the other keys are untouched:
```bash
grep -E '^(usernames|repos|since|until|metrics):' config.yml
```

Expected: those five keys, in that order, present exactly once each.

Verify the file parses and has the correct shape:
```bash
python3 - <<'PY'
import yaml
d = yaml.safe_load(open("config.yml"))
assert set(d) == {"usernames", "repos", "since", "until", "metrics"}, d.keys()
assert d["metrics"] == ["authoring", "collaboration", "team_share"], d["metrics"]
assert isinstance(d["usernames"], list) and len(d["usernames"]) >= 1
assert isinstance(d["repos"], list)
print("OK")
PY
```

Expected: `OK`.

If `python3 -c "import yaml"` fails (PyYAML not installed), fall back to:
```bash
python3 - <<'PY'
lines = [l.rstrip() for l in open("config.yml")]
assert "metrics:" in lines
mi = lines.index("metrics:")
assert lines[mi+1:mi+4] == ["  - authoring", "  - collaboration", "  - team_share"], lines[mi+1:mi+4]
print("OK")
PY
```
Expected: `OK`.

- [ ] **Step 5: Confirm `config.yml` is still git-ignored, then remove the backup**

```bash
git status --ignored
```

Expected: working tree clean, `config.yml` listed under Ignored files, `config.yml.bak` listed under Untracked files.

Remove the backup:
```bash
rm config.yml.bak
```

Verify:
```bash
test ! -e config.yml.bak && echo OK
git status --ignored
```

Expected: `OK`, then a clean working tree with only `config.yml` under Ignored files.

- [ ] **Step 6: No commit for this task**

`config.yml` is intentionally not tracked. Do **not** run `git add -f`. This task ends without a git commit; Task 1's commit is the only one produced by this plan.

Sanity:
```bash
git log --oneline -3
```

Expected: the top commit is Task 1's `docs: mark analysis-config spec metrics field as superseded`, then the design-spec commit `6a7eb18`, then `af81056 chore: ignore config.yml ...`.

---

## Success Criteria (final verification)

Run all five checks; each must pass:

```bash
grep -q 'Superseded by' docs/superpowers/specs/2026-07-01-analysis-config-file-design.md && echo "parent spec marked superseded: OK"
grep -q 'Allowed values are now `authoring`, `collaboration`, `team_share`' docs/superpowers/specs/2026-07-01-analysis-config-file-design.md && echo "parent spec metrics row updated: OK"
python3 -c "import yaml; d=yaml.safe_load(open('config.yml')); assert d['metrics']==['authoring','collaboration','team_share']; print('config.yml metrics updated: OK')"
git check-ignore -v config.yml >/dev/null && echo "config.yml still ignored: OK"
[ -z "$(git status --porcelain)" ] && echo "working tree clean: OK"
```

Expected: five `OK` lines.
