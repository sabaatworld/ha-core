"""Build the LIFX Ultimate HACS distribution from the Core integration source."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

DOMAIN = "lifx"
INTEGRATION_NAME = "LIFX Ultimate"
REPOSITORY = "sabaatworld/ha-lifx-ultimate"
SOURCE_REPOSITORY = "sabaatworld/ha-core"
EXCLUDED_SOURCE_FILES = {"AGENTS.md", "strings.json"}


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-revision", required=True)
    return parser.parse_args()


def _ignore_source_files(directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name in EXCLUDED_SOURCE_FILES}
    ignored.update(name for name in names if name == "__pycache__")
    return ignored


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _write_readme(destination: Path, source_revision: str, version: str) -> None:
    readme = "\n".join(
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

## Updates

This repository is generated automatically from
""",
            f"[`{SOURCE_REPOSITORY}`](https://github.com/{SOURCE_REPOSITORY}) whenever its",
            """LIFX source changes. HACS tracks normal commits; no manual release selection is
required.""",
            f"Generated from source revision `{source_revision}` as version `{version}`.",
            "",
        )
    )
    (destination / "README.md").write_text(readme)


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
    source: Path, destination: Path, version: str, source_revision: str
) -> None:
    """Write one complete HACS custom-integration repository."""
    translation = source / "translations" / "en.json"
    if not translation.is_file():
        msg = f"Missing required custom-integration translation: {translation}"
        raise FileNotFoundError(msg)

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
    _write_json(destination / "hacs.json", {"name": INTEGRATION_NAME})
    _write_readme(destination, source_revision, version)
    _write_validation_workflow(destination)


def main() -> None:
    """Run the command-line exporter."""
    arguments = _parse_arguments()
    export_distribution(
        arguments.source,
        arguments.destination,
        arguments.version,
        arguments.source_revision,
    )


if __name__ == "__main__":
    main()
