from __future__ import annotations

from pathlib import Path

from dtdpgis.core.registry import get_tool
from dtdpgis.core.workspace import ProjectWorkspace
from tests.fixtures import make_m4_inputs


M4_PROFILE = {
    "m4": {
        "raster_types": {"by_asset_id": {}, "patterns": {}},
        "fill_holes": {
            "dem": {"max_hole_pixels": 20, "max_dist": 10, "smooth_iterations": 0},
            "dom": {"max_hole_pixels": 20, "max_dist": 10, "smooth_iterations": 0},
            "classification": {"action": "mark_only"},
        },
        "outlier_fix": {
            "dem": {"window_size": 3, "soft_threshold": 30, "hard_threshold": 80, "action": "repair_hard_only"},
            "dom": {"window_size": 3, "soft_threshold": 20, "hard_threshold": 50, "action": "repair_hard_only"},
            "classification": {"action": "mark_only"},
        },
        "mosaic": {
            "dem": {"overlap_strategy": "mean", "seam_tolerance_pixels": 2, "seam_elevation_threshold": 0.5, "resampling": "bilinear"},
            "dom": {"overlap_strategy": "mean", "seam_tolerance_pixels": 2, "color_balance": False, "resampling": "bilinear"},
            "classification": {"overlap_strategy": "first", "resampling": "nearest", "seam_tolerance_pixels": 2},
        },
    }
}


def test_m4_pipeline(tmp_path: Path) -> None:
    project = tmp_path / "project"
    inputs = make_m4_inputs(project)
    ctx = ProjectWorkspace(project, profile=M4_PROFILE).new_run("run_004")

    inventory = get_tool("inventory.batch_scan")()
    inventory_result = inventory.execute(ctx, {"input_dirs": [str(inputs)]})
    assert inventory_result.status == "success"

    fill = get_tool("raster.batch_fill_holes")()
    fill_result = fill.execute(ctx, {})
    assert fill_result.status == "success"
    assert (ctx.outputs_dir / "raster_filled").exists()
    assert (ctx.outputs_dir / "raster_hole_qc.csv").exists()

    outlier = get_tool("raster.batch_outlier_fix")()
    outlier_result = outlier.execute(ctx, {})
    assert outlier_result.status == "success"
    assert (ctx.outputs_dir / "raster_outlier_fixed").exists()
    assert (ctx.outputs_dir / "raster_outlier_qc.csv").exists()

    mosaic = get_tool("raster.batch_mosaic")()
    mosaic_result = mosaic.execute(ctx, {})
    assert mosaic_result.status == "success"
    assert (ctx.outputs_dir / "raster_mosaic").exists()
    assert (ctx.outputs_dir / "raster_mosaic_qc.csv").exists()
    assert list((ctx.outputs_dir / "raster_mosaic").glob("*.tif"))
