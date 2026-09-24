# LIFX rebase onto upstream/dev with squash to 4 commits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse 27 dev commits to 4 thematic commits, then rebase onto latest upstream/dev, resolving conflicts.

**Architecture:** Soft-reset by file paths into 4 staged commits on old base, then replay onto upstream/dev via rebase so only 4 units need conflict resolution; runtime commit absorbs the lifx-async migration pain, other three replay nearly clean.

**Tech Stack:** git (soft reset, interactive staging, rebase), Home Assistant lifx integration, pytest for verification.

**Spec:** User request in chat 2026-09-23: dev-only scope (leave lifx-revert-virtual-power-off and lifx-startup-logging branches untouched), 4-commit shape chosen by agent. Base: cea0a35613d, HEAD: 52e02730198, upstream/dev: 4f0366f3fce (1124 commits ahead).

## Global Constraints

- Do NOT amend, squash, or rebase commits already pushed to a PR branch after PR opened — N/A here (dev is fork-private, no PR open).
- Never rename lifx domain, component dir, or create second domain — technical domain stays `lifx`.
- Do NOT touch lifx-revert-virtual-power-off or lifx-startup-logging branches.
- Leave untracked `config.json` alone (do not `git add .` blindly).
- Verify with `uv run --no-sync pytest tests/components/lifx/` and `python3 -m script.translations develop --integration lifx` where noted.

---

## File Structure

Squash groups by final tree state (`cea0a35613d..HEAD`, 44 files):

- **Commit A — runtime:** `homeassistant/components/lifx/{__init__,button,config_flow,const,coordinator,diagnostics,light,manager,number.py(new),parallel.py(new),parallel_group.py(new),strings.json,translations/en.json}`, `tests/components/lifx/{test_config_flow,test_light,test_number(new),test_parallel(new),test_parallel_group(new)}.py`
- **Commit B — HACS:** `.github/workflows/publish-lifx-ultimate.yml`, `.gitignore`, `homeassistant/components/lifx/{manifest.json,README_FEATURES.md,brand/icon.png}`, `script/lifx_ultimate_hacs_export.py`, `script/lifx_ultimate_hacs_assets/LICENSE`, `tests/components/lifx/test_hacs_export.py`
- **Commit C — docs:** `docs/superpowers/plans/2026-07-1*.md` (10 files), `docs/superpowers/specs/2026-07-*.md` (4 files)
- **Commit D — tooling:** `script/sync_custom_component.sh`, `AGENTS.md`, `CLAUDE.md`
- **Conflict epicenter:** upstream `a3b9ccc22fc` (lifx-async migration, 38 files, ~13k insertions) rewrites every file Commit A touches.

---

### Task 1: Safety backup and preflight

**Files:**
- Modify: none (verification only)

**Interfaces:**
- Consumes: dev at 52e02730198, origin/dev, existing backup dev-backup-20260923
- Produces: fresh timestamped backup branch, clean-tree confirmation, recorded SHAs

- [ ] **Step 1: Confirm clean tree and record SHAs**

```bash
git status --short
git rev-parse HEAD
git rev-parse cea0a35613d5ac4c3dc441c8cfe5f0497220ead0
git rev-parse upstream/dev
git branch --show-current
```

Run: commands above.
Expected: branch `dev`, HEAD `52e02730198`, `config.json` is the only `??` entry, no modified files.

- [ ] **Step 2: Create fresh backup (dev-backup-20260923 already exists, so timestamp it)**

```bash
git branch dev-backup-20260923-precollapse dev
git branch -vv | grep backup
```

Run: commands above.
Expected: new backup branch points at `52e02730198`.

- [ ] **Step 3: Refresh upstream pointer**

```bash
git fetch upstream dev
git rev-parse upstream/dev
git log --oneline cea0a35613d..upstream/dev -- homeassistant/components/lifx/ | head -10
```

Run: commands above.
Expected: upstream/dev near `4f0366f3fce`; lifx touches include `a3b9ccc22fc`, `0349bfc8bcc`, `c8035dc829a`.

---

### Task 2: Collapse 27 commits into 4 via soft reset

**Files:**
- Modify: index only (no working-tree edits); restages the 44-file diff below in 4 groups

**Interfaces:**
- Consumes: full staged diff from `git reset --soft cea0a35613d`
- Produces: 4 new commits A/B/C/D on top of cea0a35613d (verified by `git log --oneline cea0a35613d..HEAD`)

- [ ] **Step 1: Soft reset to old base (keeps worktree, stages everything)**

```bash
git reset --soft cea0a35613d5ac4c3dc441c8cfe5f0497220ead0
git status --short | head -60
```

Run: commands above.
Expected: ~44 staged `A/M/D` entries, worktree clean apart from `?? config.json`. Abort if worktree shows modifications.

- [ ] **Step 2: Unstage everything so each commit stages only its paths**

```bash
git reset HEAD -- .
git status --short | head -60
```

Run: commands above.
Expected: same ~44 files now unstaged (` A`/` M` in second column), still no worktree modifications.

- [ ] **Step 3: Commit A — core runtime (groups, transition entities, reliability)**

```bash
git add homeassistant/components/lifx/__init__.py homeassistant/components/lifx/button.py homeassistant/components/lifx/config_flow.py homeassistant/components/lifx/const.py homeassistant/components/lifx/coordinator.py homeassistant/components/lifx/diagnostics.py homeassistant/components/lifx/light.py homeassistant/components/lifx/manager.py homeassistant/components/lifx/number.py homeassistant/components/lifx/parallel.py homeassistant/components/lifx/parallel_group.py homeassistant/components/lifx/strings.json homeassistant/components/lifx/translations/en.json tests/components/lifx/test_config_flow.py tests/components/lifx/test_light.py tests/components/lifx/test_number.py tests/components/lifx/test_parallel.py tests/components/lifx/test_parallel_group.py
git status --short | head -30
git commit -m "feat(lifx): precise device groups, transition entities and reliability

Groups multiple physical bulbs into one precise parallel group with
staged-ACK preemption, keepalive and notification-free commands,
partial-availability handling, and per-duration transition number
entities with cross-fade support."
```

Run: commands above.
Expected: commit succeeds; `git show --stat HEAD` lists only runtime/test files above.

- [ ] **Step 4: Commit B — HACS publisher and export**

```bash
git add .github/workflows/publish-lifx-ultimate.yml .gitignore homeassistant/components/lifx/manifest.json homeassistant/components/lifx/README_FEATURES.md homeassistant/components/lifx/brand/icon.png script/lifx_ultimate_hacs_export.py script/lifx_ultimate_hacs_assets/LICENSE tests/components/lifx/test_hacs_export.py
git status --short | head -30
git commit -m "feat(lifx): HACS Ultimate publisher and versioned export

Adds the deploy-key publisher workflow, versioned HACS export script
with branding assets and repository metadata, and export tests."
```

Run: commands above.
Expected: commit succeeds; stat shows only HACS/workflow/script/manifest/README/test_hacs files.

- [ ] **Step 5: Commit C — plans and specs docs**

```bash
git add docs/superpowers/plans/2026-07-18-lifx-device-group-reliability.md docs/superpowers/plans/2026-07-19-lifx-device-group-no-ack.md docs/superpowers/plans/2026-07-19-lifx-device-group-staged-ack-preemption.md docs/superpowers/plans/2026-07-19-lifx-group-virtual-off-correction.md docs/superpowers/plans/2026-07-19-lifx-virtual-power-state.md docs/superpowers/plans/2026-07-20-lifx-cross-fade-and-dynamic-group-endpoints.md docs/superpowers/plans/2026-07-20-lifx-group-keepalive-and-notification-free-commands.md docs/superpowers/plans/2026-07-21-lifx-ultimate-hacs-distribution.md docs/superpowers/plans/2026-07-21-lifx-ultimate-versioned-releases-and-readme.md docs/superpowers/plans/2026-07-23-lifx-device-group-partial-availability.md docs/superpowers/specs/2026-07-19-lifx-device-group-no-ack-design.md docs/superpowers/specs/2026-07-20-lifx-cross-fade-and-dynamic-group-endpoints-design.md docs/superpowers/specs/2026-07-20-lifx-group-keepalive-and-notification-free-commands-design.md docs/superpowers/specs/2026-07-21-lifx-ultimate-release-and-readme-design.md
git status --short | head -30
git commit -m "docs(lifx): group reliability and HACS distribution plans"
```

Run: commands above.
Expected: commit succeeds. If any listed docs file errors as missing, list actual leftovers with `git status --short` and `git add docs/superpowers` for exactly those leftovers, then commit.

- [ ] **Step 6: Commit D — sync script and dev-docs consolidation**

```bash
git add script/sync_custom_component.sh AGENTS.md CLAUDE.md
git status --short
git commit -m "chore(lifx): custom-component sync script and dev docs consolidation"
```

Run: commands above.
Expected: commit succeeds; afterwards `git status --short` shows only `?? config.json`.

- [ ] **Step 7: Verify 4-commit stack and tree identity**

```bash
git log --oneline cea0a35613d..HEAD
git rev-list --count cea0a35613d..HEAD
git diff dev-backup-20260923-precollapse --stat | tail -5
git diff dev-backup-20260923-precollapse --quiet && echo TREE-IDENTICAL || echo TREE-DIFFERS
```

Run: commands above.
Expected: count is `4`; final `git diff` against the pre-collapse backup is empty (`TREE-IDENTICAL`). If it differs, stop — a file landed in the wrong commit.

---

### Task 3: Rebase 4-commit stack onto upstream/dev

**Files:**
- Modify: conflicted files only, resolved in worktree during rebase

**Interfaces:**
- Consumes: 4-commit stack on cea0a35613d from Task 2
- Produces: 4-commit stack on upstream/dev tip, or a stopped rebase with conflict list

- [ ] **Step 1: Start the rebase**

```bash
git rebase upstream/dev
```

Run: command above.
Expected: Commit A stops with conflicts (lifx-async rewrite); B/C/D wait. Do NOT pass `--skip` blindly. If it applies cleanly (unlikely), skip to Task 5.

- [ ] **Step 2: If rebase stops, triage by commit**

```bash
git status --short | head -40
git log --oneline -3
```

Run: commands above.
Expected: `You are currently rebasing` with `both modified` entries concentrated in `homeassistant/components/lifx/{coordinator,light,manager,config_flow,const,__init__}` and `tests/components/lifx/test_light.py`. New files (`parallel.py`, `parallel_group.py`, `number.py`) appear as `added by us`, not conflicts.

---

### Task 4: Resolve conflicts per commit (expect Commit A to be heavy)

**Files:**
- Modify: each `both modified` file below; keep our new files, adapt their imports to lifx-async

**Interfaces:**
- Consumes: stopped rebase from Task 3, upstream lifx-async tree
- Produces: `git rebase --continue` per commit until rebase completes

- [ ] **Step 1: Resolve Commit A — accept upstream lifx-async, re-apply our deltas**

For each conflicted file, open the `<<<<<<<` hunks. Default rule: keep upstream's lifx-async structure (imports, `lifx-async` client, coordinator base, config-flow, services.py split from `0349bfc8bcc`/`c8035dc829a`), then re-apply our behavior on top (group dispatch, virtual-off removal already excluded per dev-only scope, transition numbers, LOGGER name from `bae9b491771`).

```bash
git status --short | grep -E "^(UU|AA|UD|DU)"
git diff --name-only --diff-filter=U
```

Run: commands above to get the conflict list.
Expected high-conflict files: `coordinator.py`, `light.py`, `manager.py`, `config_flow.py`, `__init__.py`, `const.py`, `strings.json`, `test_light.py`, `test_config_flow.py`. Low/no-conflict: `parallel.py`, `parallel_group.py`, `number.py` (ours, new).

Key adaptation checklist while resolving (not placeholders — check each):
- `parallel.py` / `parallel_group.py` imports from coordinator/light must match upstream's renamed lifx-async symbols; update imports, do not revert upstream to aiolifx.
- `number.py` platform registration must follow upstream's entity/coordinator pattern, not the old aiolifx one.
- `LOGGER` vs `_LOGGER`: upstream renamed; keep upstream's name.
- `services.yaml` / `services.py`: upstream split actions into services module — our light.py service registrations must move accordingly.
- `strings.json`: keep both upstream's new keys and ours; then regenerate translations:

```bash
python3 -m script.translations develop --integration lifx
git add homeassistant/components/lifx/translations/en.json
```

- [ ] **Step 2: Run lifx tests for Commit A before continuing**

```bash
uv run --no-sync pytest tests/components/lifx/test_light.py tests/components/lifx/test_number.py tests/components/lifx/test_parallel.py tests/components/lifx/test_parallel_group.py tests/components/lifx/test_config_flow.py -q
```

Run: command above.
Expected: PASS. Fix failures before `git rebase --continue` — do not carry broken Commit A into B/C/D.

- [ ] **Step 3: Continue rebase through B/C/D (expected near-clean)**

```bash
git add -A -- ':!config.json'
git rebase --continue
git status --short | head -20
```

Run per stopped commit: resolve (B: manifest `name`/`version` vs upstream manifest changes — keep upstream deps plus our `name: LIFX Ultimate` and `version: 0.0.0`; C: docs are additive, keep both; D: AGENTS.md/CLAUDE.md keep ours), test, continue.
Expected: B/C/D each continue after at most manifest/AGENTS trivia. Repeat `git rebase --continue` until `Successfully rebased`.

- [ ] **Step 4: Abort hatch (only if resolution goes sideways)**

```bash
git rebase --abort
git log --oneline cea0a35613d..HEAD
```

Run only if conflicts prove unresolvable in this session.
Expected: back to the 4-commit stack on old base; regroup before retrying.

---

### Task 5: Verify and publish

**Files:**
- Modify: none (verification + push only)

**Interfaces:**
- Consumes: rebased 4-commit stack on upstream/dev
- Produces: green lifx tests, pushed `dev` (force-with-lease over origin/dev)

- [ ] **Step 1: Full lifx test run and translation check**

```bash
uv run --no-sync pytest tests/components/lifx/ -q
python3 -m script.translations develop --integration lifx
git status --short
```

Run: commands above.
Expected: pytest PASS; translations command produces no unexpected diff beyond Commit A.

- [ ] **Step 2: Lint the touched files**

```bash
uv run --no-sync prek run --all-files
```

Run: command above (repo standard per AGENTS.md).
Expected: clean or only pre-existing warnings unrelated to lifx.

- [ ] **Step 3: Push rebased dev (force-with-lease, origin/dev will diverge by design)**

```bash
git log --oneline upstream/dev..HEAD
git push --force-with-lease origin dev
```

Run: commands above.
Expected: log shows exactly 4 commits; push succeeds. If lease fails (someone pushed origin/dev), stop and `git fetch origin` before retrying.

---

## Self-Review

1. **Spec coverage:** 27→4 squash (Task 2) covers collapse request; upstream rebase + lifx-async conflict handling (Tasks 3–4) covers pull-and-reapply; dev-only scope and config.json guardrails cover user answers; verification/push (Task 5) closes the loop. Gaps: none — side branches explicitly excluded.
2. **Placeholder scan:** no TBD/TODO; every step has exact `git add` paths, commit messages, and commands with expected outputs.
3. **Type consistency:** SHAs (cea0a35, 52e02730, 4f0366f3), branch names (dev, upstream/dev, backups), and Commit A–D file sets used consistently across tasks; manifest ownership assigned once (Commit B) to avoid double-staging.

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-09-23-lifx-rebase-squash.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
