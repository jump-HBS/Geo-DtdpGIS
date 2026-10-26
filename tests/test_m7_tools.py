from __future__ import annotations

from pathlib import Path

from dtdpgis.core.workspace import ProjectWorkspace
from dtdpgis.modules.m7.framework import run_available_plugins


def test_m7_sample_plugin(tmp_path: Path) -> None:
    project = tmp_path / "project"
    ctx = ProjectWorkspace(project).new_run("run_007")
    summary = run_available_plugins(ctx, plugin_ids=["sample_echo"])
    assert summary["plugins"] == 1
    assert summary["success"] == 1
    assert (ctx.outputs_dir / "ai_plugin_qc.csv").exists()
    assert (ctx.outputs_dir / "ai_plugin_manifest.csv").exists()
