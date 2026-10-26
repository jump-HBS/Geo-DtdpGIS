"""Small command line entry point for exercising DtdpGIS tools."""

from __future__ import annotations

import argparse
import json

from dtdpgis.core.registry import get_tool
from dtdpgis.core.workspace import ProjectWorkspace


def run_m1(project: str, inputs: list[str], run_id: str | None) -> None:
    workspace = ProjectWorkspace(project)
    ctx = workspace.new_run(run_id)

    inventory = get_tool("inventory.batch_scan")()
    inventory_result = inventory.execute(ctx, {"input_dirs": inputs})
    print(json.dumps({"tool": inventory.spec.id, "status": inventory_result.status, "summary": inventory_result.summary}, ensure_ascii=False))

    ingest = get_tool("ingest.batch_qc")()
    ingest_result = ingest.execute(ctx, {})
    print(json.dumps({"tool": ingest.spec.id, "status": ingest_result.status, "summary": ingest_result.summary}, ensure_ascii=False))

    pointcloud = get_tool("pointcloud.batch_stats")()
    pointcloud_result = pointcloud.execute(ctx, {})
    print(json.dumps({"tool": pointcloud.spec.id, "status": pointcloud_result.status, "summary": pointcloud_result.summary}, ensure_ascii=False))


def run_m2(project: str, run_id: str) -> None:
    workspace = ProjectWorkspace(project)
    ctx = workspace.new_run(run_id)

    audit = get_tool("crs.audit")()
    audit_result = audit.execute(ctx, {})
    print(json.dumps({"tool": audit.spec.id, "status": audit_result.status, "summary": audit_result.summary}, ensure_ascii=False))

    vector = get_tool("crs.batch_reproject_vector")()
    vector_result = vector.execute(ctx, {})
    print(json.dumps({"tool": vector.spec.id, "status": vector_result.status, "summary": vector_result.summary}, ensure_ascii=False))

    raster = get_tool("crs.batch_reproject_raster")()
    raster_result = raster.execute(ctx, {})
    print(json.dumps({"tool": raster.spec.id, "status": raster_result.status, "summary": raster_result.summary}, ensure_ascii=False))


def run_m3(project: str, run_id: str) -> None:
    workspace = ProjectWorkspace(project)
    ctx = workspace.new_run(run_id)

    topology = get_tool("topology.batch_repair")()
    topology_result = topology.execute(ctx, {})
    print(json.dumps({"tool": topology.spec.id, "status": topology_result.status, "summary": topology_result.summary}, ensure_ascii=False))

    attribute = get_tool("attribute.batch_normalize")()
    attribute_result = attribute.execute(ctx, {})
    print(json.dumps({"tool": attribute.spec.id, "status": attribute_result.status, "summary": attribute_result.summary}, ensure_ascii=False))


def run_m4(project: str, run_id: str) -> None:
    workspace = ProjectWorkspace(project)
    ctx = workspace.new_run(run_id)

    fill = get_tool("raster.batch_fill_holes")()
    fill_result = fill.execute(ctx, {})
    print(json.dumps({"tool": fill.spec.id, "status": fill_result.status, "summary": fill_result.summary}, ensure_ascii=False))

    outlier = get_tool("raster.batch_outlier_fix")()
    outlier_result = outlier.execute(ctx, {})
    print(json.dumps({"tool": outlier.spec.id, "status": outlier_result.status, "summary": outlier_result.summary}, ensure_ascii=False))

    mosaic = get_tool("raster.batch_mosaic")()
    mosaic_result = mosaic.execute(ctx, {})
    print(json.dumps({"tool": mosaic.spec.id, "status": mosaic_result.status, "summary": mosaic_result.summary}, ensure_ascii=False))


def run_m5(project: str, run_id: str) -> None:
    workspace = ProjectWorkspace(project)
    ctx = workspace.new_run(run_id)

    ground = get_tool("pointcloud.batch_ground_filter")()
    ground_result = ground.execute(ctx, {})
    print(json.dumps({"tool": ground.spec.id, "status": ground_result.status, "summary": ground_result.summary}, ensure_ascii=False))

    thin = get_tool("pointcloud.batch_thin")()
    thin_result = thin.execute(ctx, {})
    print(json.dumps({"tool": thin.spec.id, "status": thin_result.status, "summary": thin_result.summary}, ensure_ascii=False))

    oblique = get_tool("oblique.batch_quality_check")()
    oblique_result = oblique.execute(ctx, {})
    print(json.dumps({"tool": oblique.spec.id, "status": oblique_result.status, "summary": oblique_result.summary}, ensure_ascii=False))


def run_m6(project: str, run_id: str) -> None:
    workspace = ProjectWorkspace(project)
    ctx = workspace.new_run(run_id)

    spatial = get_tool("vector.batch_spatial_relation")()
    spatial_result = spatial.execute(ctx, {})
    print(json.dumps({"tool": spatial.spec.id, "status": spatial_result.status, "summary": spatial_result.summary}, ensure_ascii=False))

    extrude = get_tool("building.batch_extrude")()
    extrude_result = extrude.execute(ctx, {})
    print(json.dumps({"tool": extrude.spec.id, "status": extrude_result.status, "summary": extrude_result.summary}, ensure_ascii=False))


def run_m7(project: str, run_id: str, plugins: list[str] | None, input_path: str | None) -> None:
    from dtdpgis.modules.m7.framework import run_available_plugins

    workspace = ProjectWorkspace(project)
    ctx = workspace.new_run(run_id)
    params = {"input_path": input_path} if input_path else {}
    summary = run_available_plugins(ctx, plugin_ids=plugins, params=params)
    print(json.dumps({"module": "m7", "summary": summary}, ensure_ascii=False, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(prog="dtdpgis", description="DtdpGIS batch processing tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    m1_parser = subparsers.add_parser("m1", help="Run all M1 tools")
    m1_parser.add_argument("--project", required=True)
    m1_parser.add_argument("--inputs", nargs="+", required=True)
    m1_parser.add_argument("--run-id", default=None)

    m2_parser = subparsers.add_parser("m2", help="Run all M2 tools")
    m2_parser.add_argument("--project", required=True)
    m2_parser.add_argument("--run-id", required=True)

    m3_parser = subparsers.add_parser("m3", help="Run all M3 tools")
    m3_parser.add_argument("--project", required=True)
    m3_parser.add_argument("--run-id", required=True)

    m4_parser = subparsers.add_parser("m4", help="Run all M4 tools")
    m4_parser.add_argument("--project", required=True)
    m4_parser.add_argument("--run-id", required=True)

    m5_parser = subparsers.add_parser("m5", help="Run all M5 tools")
    m5_parser.add_argument("--project", required=True)
    m5_parser.add_argument("--run-id", required=True)

    m6_parser = subparsers.add_parser("m6", help="Run all M6 tools")
    m6_parser.add_argument("--project", required=True)
    m6_parser.add_argument("--run-id", required=True)

    m7_parser = subparsers.add_parser("m7", help="Run M7 AI plugins")
    m7_parser.add_argument("--project", required=True)
    m7_parser.add_argument("--run-id", required=True)
    m7_parser.add_argument("--plugin", action="append", default=None)
    m7_parser.add_argument("--input", default=None)
    args = parser.parse_args()

    if args.command == "m1":
        run_m1(args.project, args.inputs, args.run_id)
    elif args.command == "m2":
        run_m2(args.project, args.run_id)
    elif args.command == "m3":
        run_m3(args.project, args.run_id)
    elif args.command == "m4":
        run_m4(args.project, args.run_id)
    elif args.command == "m5":
        run_m5(args.project, args.run_id)
    elif args.command == "m6":
        run_m6(args.project, args.run_id)
    elif args.command == "m7":
        run_m7(args.project, args.run_id, args.plugin, args.input)


if __name__ == "__main__":
    main()
