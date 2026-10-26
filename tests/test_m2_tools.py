from __future__ import annotations

import csv
import json
from pathlib import Path

import geopandas as gpd
import rasterio

from dtdpgis.core.registry import get_tool
from dtdpgis.core.workspace import ProjectWorkspace
from tests.fixtures import make_m2_inputs


M2_PROFILE = {
    "m2": {
        "project_crs": {
            "target_epsg": 4548,
            "target_central_meridian": 117,
            "target_unit": "metre",
            "vertical_datum": "1985国家高程基准",
        },
        "audit": {
            "assumed_crs": {},
            "checks": {
                "CRS_MISSING": {"level": "fail"},
                "CRS_DECLARED_UNKNOWN": {"level": "fail"},
                "CRS_VALUE_INCONSISTENT": {"level": "fail"},
                "CRS_ASSUMED_BY_CONFIG": {"level": "warn"},
                "UNIT_MISMATCH": {"level": "warn"},
                "CM_OUT_OF_PROJECT_BAND": {"level": "warn"},
                "DATUM_TRANSFORM_REQUIRED": {"level": "warn"},
                "VERTICAL_DATUM_UNDECLARED": {"level": "warn"},
            },
        },
        "vector_reproject": {"output_format": "GPKG", "always_xy": True, "allow_ballpark": False},
        "raster_reproject": {
            "resampling": {"DEM": "bilinear", "DOM": "bilinear", "thematic": "nearest"},
            "target_resolution": None,
            "grid_align": "project",
            "compression": "LZW",
            "tiled": True,
            "nodata": None,
        },
    }
}


def test_m2_pipeline(tmp_path: Path) -> None:
    project = tmp_path / "project"
    inputs = make_m2_inputs(project)
    workspace = ProjectWorkspace(project, profile=M2_PROFILE)
    ctx = workspace.new_run("run_002")

    inventory = get_tool("inventory.batch_scan")()
    inventory_result = inventory.execute(ctx, {"input_dirs": [str(inputs)]})
    assert inventory_result.status == "success"

    audit = get_tool("crs.audit")()
    audit_result = audit.execute(ctx, {})
    assert audit_result.status == "success"
    audit_path = ctx.outputs_dir / "crs_audit.csv"
    assert audit_path.exists()
    with audit_path.open(encoding="utf-8-sig") as handle:
        audit_rows = list(csv.DictReader(handle))
    assert any(row["rule_id"] == "UNIT_MISMATCH" for row in audit_rows)

    vector = get_tool("crs.batch_reproject_vector")()
    vector_result = vector.execute(ctx, {})
    assert vector_result.status == "success"
    assert (ctx.outputs_dir / "vector_reproject_manifest.csv").exists()
    vector_outputs = list((ctx.outputs_dir / "vector").glob("*.gpkg"))
    assert vector_outputs
    reprojected = gpd.read_file(vector_outputs[0])
    assert reprojected.crs.to_epsg() == 4548

    raster = get_tool("crs.batch_reproject_raster")()
    raster_result = raster.execute(ctx, {})
    assert raster_result.status == "success"
    assert (ctx.outputs_dir / "raster_reproject_manifest.csv").exists()
    raster_outputs = list((ctx.outputs_dir / "raster").glob("*.tif"))
    assert raster_outputs
    with rasterio.open(raster_outputs[0]) as dataset:
        assert dataset.crs.to_epsg() == 4548
