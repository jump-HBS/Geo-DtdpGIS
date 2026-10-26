"""建筑三维挤出工具"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dtdpgis.core.models import ItemResult, QCLevel, RunContext, ToolResult, ToolSpec
from dtdpgis.core.registry import register
from dtdpgis.core.tools import BaseTool
from dtdpgis.modules.m6._common import QC_FIELDS, resolve_vector_inputs, role_matches, write_csv


MANIFEST_FIELDS = [
    "run_id",
    "asset_id",
    "relative_path",
    "source_path",
    "output_gpkg",
    "output_geojson",
    "building_count",
    "success_count",
    "failed_count",
    "source_stage",
    "status",
    "message",
]


@register
class BuildingBatchExtrude(BaseTool):
    spec = ToolSpec(
        id="building.batch_extrude",
        name="建筑三维挤出",
        module="m6",
        description="按 height 或 floors 字段把建筑 footprint 挤出为三维白模。",
        params={"input_dirs": {"type": "list[path]", "title": "输入建筑矢量数据目录", "required": False}},
        inputs=["assets.json", "M3 vector manifests"],
        outputs=["building_extruded/", "building_extrude_qc.csv", "building_extrude_manifest.csv"],
    )

    def dry_run(self, ctx: RunContext, params: dict[str, Any]) -> dict[str, Any]:
        resolved = resolve_vector_inputs(ctx, params)
        return {"vector_assets": len(resolved), "note": "dry-run does not read building attributes"}

    def execute(self, ctx: RunContext, params: dict[str, Any]) -> ToolResult:
        from dtdpgis._env import ensure_runtime_env

        ensure_runtime_env()
        import geopandas as gpd
        import pandas as pd
        from shapely.geometry import MultiPolygon, Polygon

        config = ctx.profile.get("m6", {}).get("building_extrude", {})
        roles_cfg = ctx.profile.get("m6", {}).get("layer_roles", {})
        building_role = config.get("building_role", "building")
        role_cfg = roles_cfg.get(building_role)
        resolved = resolve_vector_inputs(ctx, params)

        candidates = [item for item in resolved if item["input_path"] is not None]
        match = None
        if role_cfg:
            match = next((item for item in candidates if role_matches(item["asset"], role_cfg)), None)
        if not match:
            keywords = ("building", "build", "house", "建筑", "房屋", "楼层", "floor", "footprint")
            match = next(
                (item for item in candidates
                 if any(kw in item["asset"].get("relative_path", "").lower() or kw in Path(item["asset"].get("relative_path", "")).stem.lower()
                        for kw in keywords)),
                None,
            )
        if not match:
            match = candidates[0] if candidates else None
        if not match:
            return ToolResult(tool_id=self.spec.id, run_id=ctx.run_id, status="failed", errors=[f"building role '{building_role}' not found"])

        asset = match["asset"]
        floors_field = config.get("floors_field", "floors")
        height_field = config.get("height_field", "height")
        floor_height = float(config.get("floor_height", 3.0))
        max_floors = float(config.get("max_floors", 100))
        suspicious_action = config.get("suspicious_action", "mark_only")
        output_cfg = config.get("output", {})
        output_dir = ctx.outputs_dir / "building_extruded"
        output_dir.mkdir(parents=True, exist_ok=True)
        gdf = gpd.read_file(match["input_path"], layer=match.get("layer"))

        qc_rows: list[dict[str, Any]] = []
        extruded_geometries = []
        extruded_attributes = []
        success_count = 0
        failed_count = 0
        items: list[ItemResult] = []

        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty or not geom.geom_type.startswith("Polygon"):
                failed_count += 1
                qc_rows.append(self._qc_row(ctx.run_id, asset, "EXTRUDE_FAILED", "fail", "几何无效，未挤出", "有效面几何", geom.geom_type if geom is not None else "None"))
                continue
            height = None
            if height_field in gdf.columns and pd.notna(row.get(height_field)):
                try:
                    height = float(row[height_field])
                except (TypeError, ValueError):
                    height = None
            floors = None
            if height is None and floors_field in gdf.columns and pd.notna(row.get(floors_field)):
                try:
                    floors = float(row[floors_field])
                except (TypeError, ValueError):
                    floors = None
                if floors is not None and floors > 0:
                    height = floors * floor_height
            if height is None or height <= 0:
                failed_count += 1
                qc_rows.append(self._qc_row(ctx.run_id, asset, "HEIGHT_MISSING", "fail", "层数和高度字段均无效，未挤出", "有效 height 或 floors", str(row.to_dict())))
                continue
            if floors is not None and floors > max_floors:
                qc_rows.append(self._qc_row(ctx.run_id, asset, "FLOORS_SUSPICIOUS", "warn", "层数超过阈值", f"<= {max_floors}", floors))
                if suspicious_action == "skip":
                    failed_count += 1
                    continue

            try:
                extruded = self._extrude(geom, height)
                extruded_geometries.append(extruded)
                attributes = {field: row[field] for field in gdf.columns if field != gdf.geometry.name}
                attributes["floors"] = floors if floors is not None else row.get(floors_field)
                attributes["height"] = height
                extruded_attributes.append(attributes)
                success_count += 1
                qc_rows.append(self._qc_row(ctx.run_id, asset, "BUILDING_EXTRUDED", "pass", "建筑白模挤出完成", "有效建筑", 1))
            except Exception as exc:  # noqa: BLE001
                failed_count += 1
                qc_rows.append(self._qc_row(ctx.run_id, asset, "EXTRUDE_FAILED", "fail", "单建筑挤出失败", "成功", str(exc)))

        output_gpkg = output_dir / "building_3d.gpkg"
        output_geojson = output_dir / "building_3d.geojson"
        if extruded_geometries:
            out_gdf = gpd.GeoDataFrame(extruded_attributes, geometry=extruded_geometries, crs=gdf.crs)
            if output_cfg.get("gpkg", True):
                out_gdf.to_file(output_gpkg, driver="GPKG")
            if output_cfg.get("geojson", True):
                geojson_geom = output_cfg.get("geojson_geometry", "2d_footprint")
                if geojson_geom == "2d_footprint":
                    footprint_geometries = []
                    for geom in extruded_geometries:
                        bottom = geom.geoms[0] if geom.geom_type == "MultiPolygon" else geom
                        footprint_geometries.append(Polygon([(float(c[0]), float(c[1])) for c in bottom.exterior.coords]))
                    footprints = gpd.GeoDataFrame(extruded_attributes, geometry=footprint_geometries, crs=gdf.crs)
                    footprints.to_file(output_geojson, driver="GeoJSON")
                else:
                    out_gdf.to_file(output_geojson, driver="GeoJSON")

        status = "pass" if failed_count == 0 else "warn"
        items.append(ItemResult(asset_id=asset["asset_id"], level=QCLevel.PASS if failed_count == 0 else QCLevel.WARN, message=status))
        manifest_path = ctx.outputs_dir / "building_extrude_manifest.csv"
        qc_path = ctx.outputs_dir / "building_extrude_qc.csv"
        write_csv(qc_path, QC_FIELDS, qc_rows)
        write_csv(
            manifest_path,
            MANIFEST_FIELDS,
            [
                {
                    "run_id": ctx.run_id,
                    "asset_id": asset["asset_id"],
                    "relative_path": asset["relative_path"],
                    "source_path": str(match["input_path"]),
                    "output_gpkg": str(output_gpkg) if output_gpkg.exists() else "",
                    "output_geojson": str(output_geojson) if output_geojson.exists() else "",
                    "building_count": len(gdf),
                    "success_count": success_count,
                    "failed_count": failed_count,
                    "source_stage": match["source_stage"],
                    "status": status,
                    "message": "",
                }
            ],
        )
        counts = {"building_count": len(gdf), "success": success_count, "failed": failed_count}
        return ToolResult(tool_id=self.spec.id, run_id=ctx.run_id, items=items, outputs=[str(output_dir), str(qc_path), str(manifest_path)], summary=counts)

    def _extrude(self, geom: Any, height: float) -> Any:
        from shapely.geometry import MultiPolygon, Polygon

        footprint = geom if geom.geom_type == "Polygon" else geom.geoms[0]
        coords_2d = [(float(c[0]), float(c[1])) for c in footprint.exterior.coords]
        bottom = Polygon([(x, y, 0.0) for x, y in coords_2d])
        top = Polygon([(x, y, height) for x, y in coords_2d])
        polygons = [bottom, top]
        for (x1, y1), (x2, y2) in zip(coords_2d[:-1], coords_2d[1:]):
            side = Polygon([(x1, y1, 0.0), (x2, y2, 0.0), (x2, y2, height), (x1, y1, height), (x1, y1, 0.0)])
            polygons.append(side)
        return MultiPolygon(polygons)

    def _qc_row(self, run_id, asset, rule_id, level, message, expected, actual):
        return {
            "run_id": run_id,
            "asset_id": asset["asset_id"],
            "category": asset["category"],
            "format": asset["format"],
            "relative_path": asset["relative_path"],
            "rule_id": rule_id,
            "level": level,
            "message": message,
            "expected": expected,
            "actual": actual,
        }