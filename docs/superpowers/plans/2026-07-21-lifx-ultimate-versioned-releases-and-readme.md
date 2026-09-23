# LIFX Ultimate Versioned Releases and README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate source-derived LIFX Ultimate versions, tags, and GitHub Releases while publishing a complete, source-owned HACS README.

**Architecture:** The exporter reads the base version from the LIFX source manifest, derives a globally monotonic `-v0.0.N` suffix from the previously published HACS manifest, and records the source revision/version in a generated metadata file. When that metadata already identifies the supplied source revision, it preserves the published version rather than incrementing it. The source publisher commits and annotated-tags changed exports; a generated target workflow turns pushed version tags into releases. The exporter composes the HACS README from a small generated installation/publishing header and a source-owned feature fragment.

**Tech Stack:** Python standard library exporter, GitHub Actions, Git tags/releases, Markdown, Home Assistant LIFX integration.

## Global Constraints

- Keep source manifest `domain: "lifx"`, directory `lifx`, and source version exactly `2026.7.2`.
- The first newly exported HACS version is `2026.7.2-v0.0.4`; suffixes increase globally and monotonically.
- Do not create a commit, tag, or release when a manual run exports an already-published source revision.
- A release tag exactly matches the exported HACS manifest version and is annotated.
- Keep authored feature content in `homeassistant/components/lifx/README_FEATURES.md`; do not ship that file inside `custom_components/lifx`.
- Preserve the existing `lifx` configuration-entry compatibility and existing lighting behavior.
- Run Git and tests on the HA host; preserve unrelated work.

---

### Task 1: Define and test source-derived distribution versions

**Files:**
- Modify: `tests/components/lifx/test_hacs_export.py`
- Modify: `script/lifx_ultimate_hacs_export.py`

**Interfaces:**
- `export_distribution(source: Path, destination: Path, source_revision: str) -> str` returns the derived HACS version.
- Generated `.lifx-ultimate-publish.json` contains `source_revision` and `version`.

- [ ] Write failing tests that seed the destination manifest at legacy `0.0.3`, give the source manifest version `2026.7.2`, and assert an exported version of `2026.7.2-v0.0.4`; seed matching publication metadata in a second test and assert the same source revision retains its existing version.
- [ ] Run the focused test on the HA host and confirm it fails because the exporter still requires a caller-supplied version.
- [ ] Replace the exporter `--version` argument with source-manifest parsing, existing-publication metadata parsing, and a suffix parser accepting legacy `0.0.N` and modern `<base>-v0.0.N` values.
- [ ] Write `.lifx-ultimate-publish.json` and return the derived version; reject a source manifest missing a string version.
- [ ] Re-run the focused test and confirm the derived-version and metadata assertions pass.
- [ ] Commit the tested exporter/version behavior.

### Task 2: Generate the source-owned feature README

**Files:**
- Create: `homeassistant/components/lifx/README_FEATURES.md`
- Modify: `script/lifx_ultimate_hacs_export.py`
- Modify: `tests/components/lifx/test_hacs_export.py`

**Interfaces:**
- Consumes: `README_FEATURES.md` beside the source manifest.
- Produces: the root HACS `README.md`, while excluding the fragment from `custom_components/lifx`.

- [ ] Write failing assertions that the root README includes an emoji feature heading and fragment content, that it uses “📦 Automated publishing” rather than “Updates”, and that the installed integration omits `README_FEATURES.md`.
- [ ] Run the focused test and confirm it fails because the exporter neither reads nor excludes the fragment.
- [ ] Add `README_FEATURES.md` with backward-compatibility, the motivation for LIFX Ultimate, Device Group setup/timing explanation, and fade-default priority order exactly as approved in the design.
- [ ] Make `_write_readme` append the fragment after the generated header and emit publishing metadata as one paragraph; add the fragment filename to excluded runtime files.
- [ ] Re-run the focused test and confirm README composition and runtime exclusion pass.
- [ ] Commit the feature-guide behavior and content.

### Task 3: Publish matching tags and target-repository releases

**Files:**
- Modify: `.github/workflows/publish-lifx-ultimate.yml`
- Modify: `script/lifx_ultimate_hacs_export.py`
- Modify: `tests/components/lifx/test_hacs_export.py`

**Interfaces:**
- Source publisher reads `.lifx-ultimate-publish.json`, pushes the distribution commit and an annotated matching tag.
- Exporter writes target `.github/workflows/release.yml`; on a `*-v0.0.*` tag it creates a GitHub Release using `GITHUB_TOKEN` and generated notes.

- [ ] Write failing exporter assertions that `release.yml` exists and includes tag filtering, `contents: write`, and generated-release-notes behavior.
- [ ] Run the focused test and confirm it fails because the release workflow is absent.
- [ ] Generate the release workflow with `gh release create "$GITHUB_REF_NAME" --generate-notes` and least required permissions.
- [ ] Update the source workflow to omit `--version`, include exporter assets in its push paths, use full tag history, and after a changed export read the metadata version, commit, annotated-tag, and push commit plus tag together.
- [ ] Re-run exporter tests and inspect the source workflow syntax and generated release workflow content.
- [ ] Commit the tag/release publishing behavior.

### Task 4: Publish and verify the complete distribution

**Files:**
- Modify: `homeassistant/components/lifx/manifest.json`
- Test: `tests/components/lifx/test_hacs_export.py`

- [ ] Change the source manifest version from `2026.7.2-custom` to `2026.7.2`.
- [ ] Run focused exporter tests, Ruff on changed Python files, and `git diff --check` on the HA host.
- [ ] Commit and push the final source changes to `dev`.
- [ ] Wait for the source publish workflow; verify generated manifest version `2026.7.2-v0.0.4`, matching annotated tag, target GitHub Release, source revision metadata, root README rendering, and HACS validation.
- [ ] Record expected pre-existing LIFX-suite failures separately if the full suite is run and they recur.

## Self-review

- Source-manifest version, HACS suffix, tag, and release all use one derived value.
- A manual workflow run against the current source revision does not mutate the distribution.
- The HACS README no longer contains a misleading Updates section or broken paragraph construction.
- The feature fragment stays source-owned but is excluded from the runtime integration package.
