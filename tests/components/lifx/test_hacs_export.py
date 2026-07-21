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
    (source / "strings.json").write_text('{"config": {}}\n')
    (source / "manifest.json").write_text(
        json.dumps(
            {
                "domain": "lifx",
                "name": "LIFX Ultimate",
                "version": "2026.7.2-custom",
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
            "--version",
            "0.0.123",
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
    assert not (integration / "strings.json").exists()
    assert json.loads((destination / "hacs.json").read_text()) == {
        "name": "LIFX Ultimate"
    }
    manifest = json.loads((integration / "manifest.json").read_text())
    assert manifest["domain"] == "lifx"
    assert manifest["name"] == "LIFX Ultimate"
    assert manifest["version"] == "0.0.123"
    assert manifest["issue_tracker"] == "https://github.com/sabaatworld/ha-lifx-ultimate/issues"
    assert "abc1234" in (destination / "README.md").read_text()
    assert (destination / "LICENSE").read_text().lstrip().startswith("Apache License")
