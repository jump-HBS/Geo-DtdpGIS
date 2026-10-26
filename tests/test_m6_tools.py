from __future__ import annotations

from pathlib import Path

from dtdpgis.core.registry import get_tool
from dtdpgis.core.workspace import ProjectWorkspace
from tests.fixtures import make_m6_inputs


M6_PROFILE = {
    "m6": {
        "layer_roles": {
            "building": {"layer": "buildings"},
            "road": {"layer": "roads"},
            "water": {"layer": "water"},
            "boundary": {"layer": "boundary"},
            "poi": {"layer": "poi"},
            "admin": {"layer": "admin"},
        },
        "spatial_relation": {
            "tolerance": 0.01,
            "checks": {
                "ROAD_WATER_OVERLAP": {"enabled": True, "level": "warn"},
                "WATER_WATER_OVERLAP": {"enabled": True, "level": "warn", "min_overlap_area": 0.1},
                "BUILDING_OUTSIDE_BOUNDARY": {"enabled": True, "level": "warn"},
                "POI_OUTSIDE_ADMIN": {"enabled": True, "level": "fail"},
                "POI_ADMIN_MISMATCH": {"enabled": True, "level": "warn"},
                "WATER_OUTSIDE_ADMIN": {"enabled": True, "level": "warn"},
            },
            "output_issue_geometry": True,
        },
        "building_extrude": {
            "building_role": "building",
            "floors_field": "floors",
            "height_field": "height",
            "floor_height": 3.0,
            "max_floors": 100,
            "suspicious_action": "mark_only",
            "output": {"gpkg": True, "geojson": True, "geojson_geometry": "2d_footprint"},
        },
    }
}


def test_m6_pipeline(tmp_path: Path) -> None:
    project = tmp_path / "project"
    inputs = make_m6_inputs(project)
    ctx = ProjectWorkspace(project, profile=M6_PROFILE).new_run("run_006")

    inventory = get_tool("inventory.batch_scan")()
    inventory_result = inventory.execute(ctx, {"input_dirs": [str(inputs)]})
    assert inventory_result.status == "success"

    spatial = get_tool("vector.batch_spatial_relation")()
    spatial_result = spatial.execute(ctx, {})
    assert spatial_result.status == "success"
    assert (ctx.outputs_dir / "spatial_relation_qc.csv").exists()
    assert (ctx.outputs_dir / "spatial_relation_manifest.csv").exists()
    assert (ctx.outputs_dir / "vector_spatial_relation").exists()

    extrude = get_tool("building.batch_extrude")()
    extrude_result = extrude.execute(ctx, {})
    assert extrude_result.status == "success"
    assert (ctx.outputs_dir / "building_extruded").exists()
    assert (ctx.outputs_dir / "building_extruded" / "building_3d.gpkg").exists()
    assert (ctx.outputs_dir / "building_extruded" / "building_3d.geojson").exists()
