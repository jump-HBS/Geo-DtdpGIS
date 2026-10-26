from __future__ import annotations

from pathlib import Path

from dtdpgis.core.registry import get_tool
from dtdpgis.core.workspace import ProjectWorkspace
from tests.fixtures import make_m5_inputs


M5_PROFILE = {
    "m5": {
        "ground_filter": {
            "csf": {"resolution": 1.0, "threshold": 0.5, "hdiff": 0.3, "rigidness": 3, "iterations": 100, "ground_class": 2},
            "dem": {"resolution": 1.0, "output_nodata": -9999},
            "ground_ratio_warn": 0.9,
        },
        "thin": {"mode": "random", "random": {"keep_ratio": 0.5, "seed": 42}, "preserve_ground": True, "ground_class": 2},
        "oblique_qc": {
            "float_object": {"max_ground_clearance": 2.0, "max_float_tile_ratio": 0.1},
            "texture_distortion": {"sample_tiles": 20},
            "hole": {"max_hole_area_ratio": 0.2},
            "coord": {"expected_crs": "EPSG:4490", "bounds_tolerance": 1000},
            "metadata": {"require_srs": True},
        },
    }
}


def test_m5_pipeline(tmp_path: Path) -> None:
    project = tmp_path / "project"
    inputs = make_m5_inputs(project)
    ctx = ProjectWorkspace(project, profile=M5_PROFILE).new_run("run_005")

    inventory = get_tool("inventory.batch_scan")()
    inventory_result = inventory.execute(ctx, {"input_dirs": [str(inputs)]})
    assert inventory_result.status == "success"

    ground = get_tool("pointcloud.batch_ground_filter")()
    ground_result = ground.execute(ctx, {})
    assert ground_result.status == "success"
    assert (ctx.outputs_dir / "pointcloud_ground").exists()
    assert list((ctx.outputs_dir / "pointcloud_ground").glob("*.las"))
    assert list((ctx.outputs_dir / "pointcloud_ground").glob("*.tif"))

    thin = get_tool("pointcloud.batch_thin")()
    thin_result = thin.execute(ctx, {})
    assert thin_result.status == "success"
    assert (ctx.outputs_dir / "pointcloud_thinned").exists()
    assert list((ctx.outputs_dir / "pointcloud_thinned").glob("*.las"))

    oblique = get_tool("oblique.batch_quality_check")()
    oblique_result = oblique.execute(ctx, {})
    assert oblique_result.status == "success"
    assert (ctx.outputs_dir / "oblique_qc.csv").exists()
    assert (ctx.outputs_dir / "oblique_qc_manifest.csv").exists()
