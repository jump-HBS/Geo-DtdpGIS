from __future__ import annotations

import csv
from pathlib import Path

import geopandas as gpd

from dtdpgis.core.registry import get_tool
from dtdpgis.core.workspace import ProjectWorkspace
from tests.fixtures import make_m3_inputs


M3_PROFILE = {
    "m3": {
        "topology_repair": {
            "snap_tolerance": 0.001,
            "sliver_area_threshold": 0.5,
            "sliver_ratio_threshold": 3.0,
            "output_format": "GPKG",
        },
        "attribute_normalize": {
            "default_null_policy": "empty_string",
            "schemas": {
                "by_asset_id": {},
                "by_layer": {
                    "buildings": {
                        "field_map": {"name": "id", "floor": "floors", "func": "function"},
                        "required": ["id", "floors", "function"],
                        "value_domain": {"function": ["residential", "commercial", "industrial", "public"]},
                    }
                },
            },
        },
    }
}


def test_m3_pipeline(tmp_path: Path) -> None:
    project = tmp_path / "project"
    inputs = make_m3_inputs(project)
    ctx = ProjectWorkspace(project, profile=M3_PROFILE).new_run("run_003")

    inventory = get_tool("inventory.batch_scan")()
    inventory_result = inventory.execute(ctx, {"input_dirs": [str(inputs)]})
    assert inventory_result.status == "success"

    topology = get_tool("topology.batch_repair")()
    topology_result = topology.execute(ctx, {})
    assert topology_result.status == "success"
    assert (ctx.outputs_dir / "topology_repair_manifest.csv").exists()
    repaired_outputs = list((ctx.outputs_dir / "vector_repaired").glob("*.gpkg"))
    assert repaired_outputs

    attribute = get_tool("attribute.batch_normalize")()
    attribute_result = attribute.execute(ctx, {})
    assert attribute_result.status == "success"
    assert (ctx.outputs_dir / "attribute_normalize_manifest.csv").exists()
    normalized_outputs = list((ctx.outputs_dir / "vector_normalized").glob("*.gpkg"))
    assert normalized_outputs

    normalized = gpd.read_file(normalized_outputs[0])
    assert {"id", "floors", "function"}.issubset(normalized.columns)
