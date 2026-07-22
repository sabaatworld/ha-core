"""Tests for the LIFX Ultimate HACS distribution exporter."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


def test_export_creates_hacs_layout_without_core_only_files(tmp_path: Path) -> None:
    """The export is a complete custom integration without Core-only inputs."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "__init__.py").write_text('"""LIFX."""\n')
    (source / "light.py").write_text('"""Light platform."""\n')
    (source / "AGENTS.md").write_text("Development instructions\n")
    (source / "README_FEATURES.md").write_text(
        "## ✨ Why LIFX Ultimate?\n\nFeature guide content.\n"
    )
    (source / "strings.json").write_text('{"config": {}}\n')
    (source / "manifest.json").write_text(
        json.dumps(
            {
                "domain": "lifx",
                "name": "LIFX Ultimate",
                "version": "2026.7.2",
                "codeowners": ["@Djelibeybi"],
                "documentation": "https://www.home-assistant.io/integrations/lifx",
            }
        )
    )
    translations = source / "translations"
    translations.mkdir()
    (translations / "en.json").write_text('{"config": {}}\n')
    brand = source / "brand"
    brand.mkdir()
    (brand / "icon.png").write_bytes(b"official-lifx-brand")
    destination = tmp_path / "distribution"
    git_directory = destination / ".git"
    git_directory.mkdir(parents=True)
    git_metadata = git_directory / "HEAD"
    git_metadata.write_text("ref: refs/heads/main\n")

    result = subprocess.run(
        [
            sys.executable,
            "script/lifx_ultimate_hacs_export.py",
            "--source",
            str(source),
            "--destination",
            str(destination),
            "--source-revision",
            "abc1234",
        ],
        check=False,
        cwd=Path(__file__).parents[3],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    integration = destination / "custom_components" / "lifx"
    assert (integration / "__init__.py").is_file()
    assert (integration / "light.py").is_file()
    assert (integration / "translations" / "en.json").is_file()
    assert (integration / "brand" / "icon.png").is_file()
    assert git_metadata.read_text() == "ref: refs/heads/main\n"
    assert not (integration / "AGENTS.md").exists()
    assert not (integration / "README_FEATURES.md").exists()
    assert not (integration / "strings.json").exists()
    assert json.loads((destination / "hacs.json").read_text()) == {
        "name": "LIFX Ultimate"
    }
    manifest = json.loads((integration / "manifest.json").read_text())
    assert manifest["domain"] == "lifx"
    assert manifest["name"] == "LIFX Ultimate"
    assert manifest["version"] == "2026.7.2-v0.0.1"
    assert manifest["issue_tracker"] == "https://github.com/sabaatworld/ha-lifx-ultimate/issues"
    readme = (destination / "README.md").read_text()
    assert "abc1234" in readme
    assert "## 📦 Automated publishing" in readme
    assert "## Updates" not in readme
    assert "## ✨ Why LIFX Ultimate?" in readme
    assert (destination / "LICENSE").read_text().lstrip().startswith("Apache License")
    release_workflow = (destination / ".github" / "workflows" / "release.yml").read_text()
    assert '"*-v0.0.*"' in release_workflow
    assert "contents: write" in release_workflow
    assert 'gh release create "$GITHUB_REF_NAME" --generate-notes' in release_workflow


def test_export_derives_version_from_source_manifest(tmp_path: Path) -> None:
    """The next HACS version extends the source version and legacy suffix."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "__init__.py").write_text('"""LIFX."""\n')
    (source / "README_FEATURES.md").write_text("Feature guide content.\n")
    (source / "manifest.json").write_text(
        json.dumps({"domain": "lifx", "name": "LIFX Ultimate", "version": "2026.7.2"})
    )
    translations = source / "translations"
    translations.mkdir()
    (translations / "en.json").write_text('{"config": {}}\n')
    destination = tmp_path / "distribution"
    previous_manifest = destination / "custom_components" / "lifx" / "manifest.json"
    previous_manifest.parent.mkdir(parents=True)
    previous_manifest.write_text(json.dumps({"version": "0.0.3"}))

    result = subprocess.run(
        [
            sys.executable,
            "script/lifx_ultimate_hacs_export.py",
            "--source",
            str(source),
            "--destination",
            str(destination),
            "--source-revision",
            "def5678",
        ],
        check=False,
        cwd=Path(__file__).parents[3],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(
        (destination / "custom_components" / "lifx" / "manifest.json").read_text()
    )["version"] == "2026.7.2-v0.0.4"
    assert json.loads((destination / ".lifx-ultimate-publish.json").read_text()) == {
        "source_revision": "def5678",
        "version": "2026.7.2-v0.0.4",
    }

    repeated_result = subprocess.run(
        result.args,
        check=False,
        cwd=Path(__file__).parents[3],
        capture_output=True,
        text=True,
    )

    assert repeated_result.returncode == 0, repeated_result.stderr
    assert json.loads(
        (destination / "custom_components" / "lifx" / "manifest.json").read_text()
    )["version"] == "2026.7.2-v0.0.4"
