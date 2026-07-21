# LIFX Ultimate HACS Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the customized built-in LIFX integration as the automatically updated LIFX Ultimate HACS custom repository without making the upstream-tracking core fork difficult to merge.

**Architecture:** Keep the Home Assistant source integration at `homeassistant/components/lifx` with its immutable technical domain `lifx`. A source-repository GitHub Actions workflow exports a curated runtime copy to `sabaatworld/ha-lifx-ultimate`; the exported manifest is branded as LIFX Ultimate and receives a monotonic workflow-run version. The distribution repository contains only the HACS-compatible `custom_components/lifx` package and its public documentation.

**Tech Stack:** Python 3 standard library exporter, GitHub Actions, HACS custom repository format, Home Assistant custom integration translations.

## Global Constraints

- Keep `domain: "lifx"` and the component directory `lifx`; existing config entries must continue to load.
- Display `LIFX Ultimate` in the source manifest, exported manifest, HACS metadata, and user documentation.
- Retain `strings.json` in the Core source for upstream translation tooling, but never export it to HACS.
- Export the full flat `translations/en.json` file because custom integrations do not run Core's translation compiler.
- Never export development instructions, ignored bytecode, or test-only source from the integration directory.
- Publish every push to the source `dev` branch that changes the LIFX source or packaging files; do not create GitHub releases.
- The source action uses `LIFX_ULTIMATE_PUBLISH_KEY`, a write-enabled GitHub deploy key attached only to `sabaatworld/ha-lifx-ultimate`.

---

### Task 1: Separate development instructions and brand the source integration

**Files:**
- Create: `docs/lifx/AGENTS.md`
- Modify: `homeassistant/components/lifx/manifest.json`
- Delete: `homeassistant/components/lifx/AGENTS.md`

**Interfaces:**
- Produces: a source integration whose display name is `LIFX Ultimate` while `DOMAIN` remains `lifx`.

- [ ] Move the LIFX development instructions to `docs/lifx/AGENTS.md` without changing their operational constraints.
- [ ] Change only the source manifest `name` to `LIFX Ultimate`; retain `domain` and the local development `version`.
- [ ] Verify no development instruction file remains in the runtime source directory.

### Task 2: Test the HACS exporter

**Files:**
- Create: `tests/components/lifx/test_hacs_export.py`
- Create: `script/lifx_ultimate_hacs_export.py`

**Interfaces:**
- Consumes: `--source`, `--destination`, `--version`, and `--source-revision` command-line arguments.
- Produces: `custom_components/lifx/` containing runtime files and `translations/en.json`, plus HACS repository metadata.

- [ ] Write a failing subprocess test with a temporary source directory that asserts the exporter omits `AGENTS.md` and `strings.json`, writes `hacs.json` and README files, and rewrites the exported manifest to the supplied version/name.
- [ ] Run the focused test and confirm it fails because the exporter does not exist.
- [ ] Implement the exporter using `pathlib`, `shutil`, and `json`; fail clearly if the English translation file is absent.
- [ ] Run the focused test and confirm the HACS layout is complete and no source-only files are copied.

### Task 3: Publish on source updates

**Files:**
- Create: `.github/workflows/publish-lifx-ultimate.yml`

**Interfaces:**
- Consumes: `secrets.LIFX_ULTIMATE_PUBLISH_KEY`, `github.run_number`, and the checked-out source revision.
- Produces: an idempotent commit to `sabaatworld/ha-lifx-ultimate` only when an export changes.

- [ ] Check out this source repository and the target HACS repository at `main`.
- [ ] Run the exporter with `0.0.${{ github.run_number }}` so every publication has a valid, monotonically increasing SemVer manifest version.
- [ ] Commit the generated HACS layout with the source revision in the commit message and push it only when the target worktree differs.
- [ ] Restrict workflow permissions to source read; use the explicitly scoped target-repository token solely for the second checkout/push.

### Task 4: Create and validate the HACS repository

**Files:**
- Create externally: `https://github.com/sabaatworld/ha-lifx-ultimate`
- Create locally: `/Users/sabaata/Documents/Projects/ha-lifx-ultimate`

**Interfaces:**
- Produces: a public HACS custom repository with a root `hacs.json`, `README.md`, HACS validation workflow, and one `custom_components/lifx` directory.

- [ ] Create the public repository with issues enabled and a concise description.
- [ ] Run the exporter locally to create the initial distribution commit and push it from the requested local checkout.
- [ ] Add `LIFX_ULTIMATE_PUBLISH_KEY` to the source repository as the private half of a write-enabled deploy key attached only to the target repository.
- [ ] Trigger and inspect the source publication workflow.

### Task 5: Verify the source and exported repositories

**Files:**
- Test: `tests/components/lifx/test_hacs_export.py`

- [ ] Run the focused exporter test and the LIFX test suite on the Home Assistant host.
- [ ] Run Ruff and `git diff --check` on the Home Assistant host.
- [ ] Inspect the remote target tree and manifest to confirm `custom_components/lifx`, `LIFX Ultimate`, `domain: lifx`, an incremented version, and `translations/en.json` are present.
