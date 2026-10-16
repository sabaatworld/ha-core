"""Regression tests for real spawned LIFX workers under HA custom loading."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SOURCE_ROOT = Path(__file__).parents[3]
PROBE = Path(__file__).parent / "fixtures" / "parallel_spawn_probe.py"


def run_probe(
    tmp_path: Path, mode: str, operation: str
) -> subprocess.CompletedProcess[str]:
    """Run actual worker code without exposing the custom root to child imports."""
    config_root = tmp_path / "config with spaces"
    shutil.copytree(
        SOURCE_ROOT / "homeassistant" / "components" / "lifx",
        config_root / "custom_components" / "lifx",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    if mode == "custom-exported":
        config_root = tmp_path / "export with spaces"
        exported = subprocess.run(
            [
                sys.executable,
                "script/lifx_ultimate_hacs_export.py",
                "--source",
                str(SOURCE_ROOT / "homeassistant" / "components" / "lifx"),
                "--destination",
                str(config_root),
                "--source-revision",
                "spawn-regression-working",
            ],
            cwd=SOURCE_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert exported.returncode == 0, exported.stdout + exported.stderr
    if operation == "failure":
        bootstrap = config_root / "custom_components" / "lifx" / "_parallel_worker.py"
        bootstrap.write_text('raise RuntimeError("bootstrap regression sentinel")\n')
    return subprocess.run(
        [sys.executable, str(PROBE), mode, str(config_root), operation],
        cwd=SOURCE_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize("mode", ["builtin", "custom-mounted", "custom-exported"])
def test_worker_spawn_without_config_root_on_path(tmp_path: Path, mode: str) -> None:
    """Custom loading must start workers without changing the parent's imports."""
    completed = run_probe(tmp_path, mode, "startup")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    evidence = json.loads(completed.stdout)
    assert evidence == {
        "available": True,
        "startup_datagrams": 0,
        "parent_path_unchanged": True,
        "parent_environment_unchanged": True,
        "children_stopped": True,
        "dispatcher_stopped": True,
    }


@pytest.mark.parametrize("mode", ["builtin", "custom-mounted", "custom-exported"])
@pytest.mark.parametrize(
    ("operation", "replaced"), [("echo", False), ("replacement", True)]
)
def test_spawned_worker_dispatch_and_recovery(
    tmp_path: Path, mode: str, operation: str, replaced: bool
) -> None:
    """Custom command objects must unpickle in fresh and replacement workers."""
    completed = run_probe(tmp_path, mode, operation)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    evidence = json.loads(completed.stdout)
    assert evidence["available"] is True
    assert evidence["startup_datagrams"] == 0
    assert evidence["echo_completed"] is True
    assert evidence["echo_packets"] == 1
    assert evidence.get("worker_replaced", False) is replaced
    assert evidence["parent_path_unchanged"] is True
    assert evidence["parent_environment_unchanged"] is True
    assert evidence["children_stopped"] is True
    assert evidence["dispatcher_stopped"] is True


def test_failed_custom_bootstrap_cleans_up_workers(tmp_path: Path) -> None:
    """A child import/bootstrap exception must fail setup and release the pool."""
    completed = run_probe(tmp_path, "custom-mounted", "failure")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "bootstrap regression sentinel" in completed.stderr
    evidence = json.loads(completed.stdout)
    assert "closed unexpectedly" in evidence["startup_error"]
    assert evidence["children_stopped"] is True
    assert evidence["dispatcher_stopped"] is True
