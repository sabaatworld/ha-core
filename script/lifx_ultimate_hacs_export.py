"""Build the LIFX Ultimate HACS distribution from the Core integration source."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil

DOMAIN = "lifx"
INTEGRATION_NAME = "LIFX Ultimate"
REPOSITORY = "sabaatworld/ha-lifx-ultimate"
SOURCE_REPOSITORY = "sabaatworld/ha-core"
EXCLUDED_SOURCE_FILES = {"AGENTS.md", "README_FEATURES.md", "strings.json"}
ASSETS_DIRECTORY = Path(__file__).with_name("lifx_ultimate_hacs_assets")
PUBLISH_METADATA = ".lifx-ultimate-publish.json"
VERSION_SUFFIX = re.compile(r"(?:.*-)?v0\.0\.(\d+)$|0\.0\.(\d+)$")


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--source-revision", required=True)
    return parser.parse_args()


def _ignore_source_files(directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name in EXCLUDED_SOURCE_FILES}
    ignored.update(name for name in names if name == "__pycache__")
    return ignored


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _read_json(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text())
    return value if isinstance(value, dict) else None


def _source_version(source: Path) -> str:
    """Return the source integration's Home Assistant version."""
    source_manifest = _read_json(source / "manifest.json")
    if source_manifest is None or not isinstance(
        source_version := source_manifest.get("version"), str
    ):
        msg = f"Missing required string version in source manifest: {source / 'manifest.json'}"
        raise ValueError(msg)
    return source_version


def _next_version(
    source_version: str, destination: Path, source_revision: str
) -> str:
    """Return the next HACS release version for the source revision."""

    previous_metadata = _read_json(destination / PUBLISH_METADATA)
    if (
        previous_metadata is not None
        and previous_metadata.get("source_revision") == source_revision
        and isinstance(previous_version := previous_metadata.get("version"), str)
    ):
        return previous_version

    previous_manifest = _read_json(
        destination / "custom_components" / DOMAIN / "manifest.json"
    )
    previous_version = (
        previous_manifest.get("version") if previous_manifest is not None else None
    )
    match = (
        VERSION_SUFFIX.fullmatch(previous_version)
        if isinstance(previous_version, str)
        else None
    )
    counter = int(match.group(1) or match.group(2)) if match else 0
    return f"{source_version}-v0.0.{counter + 1}"


def _write_readme(
    destination: Path, source_revision: str, version: str, features: str
) -> None:
    """Write the generated HACS introduction followed by source-owned features."""
    header = "\n".join(
        (
            """# LIFX Ultimate

LIFX Ultimate is a custom Home Assistant integration that replaces the built-in
LIFX integration with the latest LIFX Ultimate enhancements.

## Installation

1. In HACS, open **Integrations** and select **Custom repositories**.""",
            f"2. Add `https://github.com/{REPOSITORY}` as an **Integration**.",
            """3. Install **LIFX Ultimate** and restart Home Assistant.

It intentionally keeps the technical domain `lifx`, so existing LIFX config
entries are retained and this package overrides Home Assistant's built-in LIFX
integration.

## 📦 Automated publishing
""",
            f"This package is generated from [`{SOURCE_REPOSITORY}`](https://github.com/{SOURCE_REPOSITORY}) at source revision `{source_revision}` and published as `{version}`. HACS uses the GitHub Release for each published version to offer updates.",
        )
    )
    (destination / "README.md").write_text(f"{header}\n{features.strip()}\n")


def _write_validation_workflow(destination: Path) -> None:
    workflow = destination / ".github" / "workflows"
    workflow.mkdir(parents=True)
    (workflow / "validate.yml").write_text(
        "name: Validate\n\n"
        "on:\n"
        "  push:\n"
        "  pull_request:\n"
        "  schedule:\n"
        "    - cron: \"0 0 * * *\"\n"
        "  workflow_dispatch:\n\n"
        "permissions: {}\n\n"
        "jobs:\n"
        "  validate-hacs:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - name: HACS validation\n"
        "        uses: hacs/action@main\n"
        "        with:\n"
        "          category: integration\n"
    )


def _write_release_workflow(destination: Path) -> None:
    workflow = destination / ".github" / "workflows"
    workflow.mkdir(parents=True, exist_ok=True)
    (workflow / "release.yml").write_text(
        "name: Release LIFX Ultimate\n\n"
        "on:\n"
        "  push:\n"
        "    tags:\n"
        "      - \"*-v0.0.*\"\n\n"
        "permissions:\n"
        "  contents: write\n\n"
        "jobs:\n"
        "  release:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - name: Create GitHub Release\n"
        f"        run: gh release create \"$GITHUB_REF_NAME\" --generate-notes --repo {REPOSITORY}\n"
        "        env:\n"
        "          GH_TOKEN: ${{ github.token }}\n"
    )


def _clear_generated_content(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for path in destination.iterdir():
        if path.name == ".git":
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def export_distribution(
    source: Path, destination: Path, source_revision: str
) -> str:
    """Write one complete HACS custom-integration repository."""
    translation = source / "translations" / "en.json"
    if not translation.is_file():
        msg = f"Missing required custom-integration translation: {translation}"
        raise FileNotFoundError(msg)
    feature_guide = source / "README_FEATURES.md"
    if not feature_guide.is_file():
        msg = f"Missing required HACS feature guide: {feature_guide}"
        raise FileNotFoundError(msg)

    source_version = _source_version(source)
    version = _next_version(source_version, destination, source_revision)
    _clear_generated_content(destination)
    integration = destination / "custom_components" / DOMAIN
    integration.parent.mkdir(parents=True)
    shutil.copytree(
        source,
        integration,
        copy_function=shutil.copyfile,
        ignore=_ignore_source_files,
    )

    manifest_path = integration / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(
        {
            "domain": DOMAIN,
            "documentation": f"https://github.com/{REPOSITORY}",
            "issue_tracker": f"https://github.com/{REPOSITORY}/issues",
            "name": INTEGRATION_NAME,
            "version": version,
        }
    )
    _write_json(manifest_path, manifest)
    _write_json(
        destination / "hacs.json",
        {
            "hide_default_branch": True,
            "homeassistant": source_version,
            "name": INTEGRATION_NAME,
        },
    )
    _write_json(
        destination / PUBLISH_METADATA,
        {"source_revision": source_revision, "version": version},
    )
    shutil.copyfile(ASSETS_DIRECTORY / "LICENSE", destination / "LICENSE")
    _write_readme(destination, source_revision, version, feature_guide.read_text())
    _write_validation_workflow(destination)
    _write_release_workflow(destination)
    return version


def main() -> None:
    """Run the command-line exporter."""
    arguments = _parse_arguments()
    export_distribution(
        arguments.source,
        arguments.destination,
        arguments.source_revision,
    )


if __name__ == "__main__":
    main()
