"""空间关系检查工具"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dtdpgis.core.models import ItemResult, QCLevel, RunContext, ToolResult, ToolSpec
from dtdpgis.core.registry import register
from dtdpgis.core.tools import BaseTool
from dtdpgis.modules.m6._common import QC_FIELDS, read_csv_rows, resolve_vector_inputs, role_matches, write_csv


MANIFEST_FIELDS = [
    "run_id",
    "asset_id",
    "relative_path",
    "source_path",
    "role",
    "feature_count",
    "issue_count",
    "source_stage",
    "status",
    "message",
]


@register
class VectorBatchSpatialRelation(BaseTool):
    spec = ToolSpec(
        id="vector.batch_spatial_relation",
        name="矢量空间关系检查",
        module="m6",
        description="检查道路/水系重叠、建筑越界、POI 行政区归属等跨图层空间关系。",
        params={"input_dirs": {"type": "list[path]", "title": "输入矢量数据目录", "required": False}},
        inputs=["assets.json", "M3 vector manifests"],
        outputs=["vector_spatial_relation/", "spatial_relation_qc.csv", "spatial_relation_manifest.csv"],
    )

    def dry_run(self, ctx: RunContext, params: dict[str, Any]) -> dict[str, Any]:
        resolved = resolve_vector_inputs(ctx, params)
        roles = ctx.profile.get("m6", {}).get("layer_roles", {})
        return {"vector_assets": len(resolved), "roles": list(roles)}

    def execute(self, ctx: RunContext, params: dict[str, Any]) -> ToolResult:
        """执行空间关系检查"""
        from dtdpgis._env import ensure_runtime_env

        ensure_runtime_env()
        import geopandas as gpd
        import pandas as pd

        config = ctx.profile.get("m6", {}).get("spatial_relation", {})
        roles_cfg = ctx.profile.get("m6", {}).get("layer_roles", {})# 图层角色配置
        resolved = resolve_vector_inputs(ctx, params)# 解析输入矢量数据
        gdfs: dict[str, gpd.GeoDataFrame] = {}# 图层 GeoDataFrame 映射
        role_assets: dict[str, dict[str, Any]] = {}# 角色到资产的映射
        for role, role_cfg in roles_cfg.items():# 遍历图层角色配置
            match = next((item for item in resolved if item["input_path"] is not None and role_matches(item["asset"], role_cfg)), None)# 查找匹配的资产
            if not match:
                continue# 跳过未找到的资产
            gdfs[role] = gpd.read_file(match["input_path"], layer=match.get("layer"))# 读取矢量数据
            role_assets[role] = match["asset"]# 记录资产信息

        checks_cfg = config.get("checks", {})# 检查配置
        qc_rows: list[dict[str, Any]] = []
        manifest_rows: list[dict[str, Any]] = []
        items: list[ItemResult] = []
        issue_frames: list[gpd.GeoDataFrame] = []

        for role in roles_cfg:
            if role not in gdfs:
                qc_rows.append({"run_id": ctx.run_id, "asset_id": "", "category": "vector", "format": "", "relative_path": "", "rule_id": "ROLE_MISSING", "level": "warn", "message": f"缺少角色 {role}", "expected": "已配置", "actual": "未找到"})

        crs_set = {gdf.crs.to_epsg() if gdf.crs else None for gdf in gdfs.values()}
        if len(crs_set) > 1:
            for role, gdf in gdfs.items():
                qc_rows.append(self._qc_row(ctx.run_id, role_assets.get(role, {}), "CRS_MISMATCH", "fail", "参与图层 CRS 不一致", "一致", gdf.crs.to_epsg() if gdf.crs else None))

        self._check_road_water(gdfs, checks_cfg, qc_rows, issue_frames, ctx)# 检查道路/水系重叠
        self._check_water_water(gdfs, checks_cfg, qc_rows, issue_frames, ctx)# 检查水系/水系重叠
        self._check_building_boundary(gdfs, checks_cfg, qc_rows, issue_frames, ctx)# 检查建筑越界
        self._check_poi_admin(gdfs, checks_cfg, qc_rows, issue_frames, ctx)# 检查 POI 行政区归属
        self._check_water_admin(gdfs, checks_cfg, qc_rows, issue_frames, ctx)# 检查水系行政区归属

        output_dir = ctx.outputs_dir / "vector_spatial_relation"
        output_dir.mkdir(parents=True, exist_ok=True)
        if config.get("output_issue_geometry", True) and issue_frames:# 输出问题几何
            issues = gpd.GeoDataFrame(pd.concat(issue_frames, ignore_index=True), crs=issue_frames[0].crs)# 合并所有问题几何
            issues.to_file(output_dir / "spatial_relation_issues.gpkg", driver="GPKG")

        for role, gdf in gdfs.items():# 遍历每个图层
            asset = role_assets[role]# 获取资产信息
            issue_count = int(any(row.get("asset_id") == asset.get("asset_id") and row["level"] in {"warn", "fail"} for row in qc_rows))# 统计问题几何数量
            status = "pass" if not issue_count else "warn"# 状态判断
            items.append(ItemResult(asset_id=asset.get("asset_id", role), level=QCLevel(status), message=status))# 记录检查结果
            manifest_rows.append(
                {
                    "run_id": ctx.run_id,
                    "asset_id": asset.get("asset_id", ""),
                    "relative_path": asset.get("relative_path", ""),
                    "source_path": "",
                    "role": role,
                    "feature_count": len(gdf),
                    "issue_count": issue_count,
                    "source_stage": "m6",
                    "status": status,
                    "message": "",
                }
            )

        qc_path = ctx.outputs_dir / "spatial_relation_qc.csv"
        manifest_path = ctx.outputs_dir / "spatial_relation_manifest.csv"
        write_csv(qc_path, QC_FIELDS, qc_rows)# 写入检查结果
        write_csv(manifest_path, MANIFEST_FIELDS, manifest_rows)# 写入清单
        counts = {"roles": len(gdfs), "issues": sum(1 for row in qc_rows if row["level"] in {"warn", "fail"})}# 统计检查结果
        return ToolResult(tool_id=self.spec.id, run_id=ctx.run_id, items=items, outputs=[str(output_dir), str(qc_path), str(manifest_path)], summary=counts)# 返回工具结果

    def _check_road_water(self, gdfs, checks_cfg, qc_rows, issue_frames, ctx):
        if "road" not in gdfs or "water" not in gdfs:# 检查道路/水系重叠
            return
        union = gdfs["water"].geometry.union_all()# 合并所有水系面
        hits = gdfs["road"][gdfs["road"].geometry.intersects(union)]# 筛选出与水系面相交的道路
        if len(hits):
            qc_rows.append({"run_id": ctx.run_id, "asset_id": "", "category": "vector", "format": "", "relative_path": "", "rule_id": "ROAD_WATER_OVERLAP", "level": "warn", "message": "道路穿越水系面", "expected": "无穿越", "actual": f"{len(hits)} 处"})
            if not hits.empty:
                issue_frames.append(hits.assign(rule_id="ROAD_WATER_OVERLAP", level="warn", message="道路穿越水系面"))

    def _check_water_water(self, gdfs, checks_cfg, qc_rows, issue_frames, ctx):
        if "water" not in gdfs:# 检查水系重叠
            return
        min_area = float(checks_cfg.get("WATER_WATER_OVERLAP", {}).get("min_overlap_area", 1.0))# 最小重叠面积
        geoms = list(gdfs["water"].geometry)# 获取所有水系面
        count = 0
        for i in range(len(geoms)):# 遍历所有水系面
            for j in range(i + 1, len(geoms)):# 遍历所有水系面
                if geoms[i].intersects(geoms[j]) and geoms[i].intersection(geoms[j]).area >= min_area:
                    count += 1
        if count:
            qc_rows.append({"run_id": ctx.run_id, "asset_id": "", "category": "vector", "format": "", "relative_path": "", "rule_id": "WATER_WATER_OVERLAP", "level": "warn", "message": "水系面之间存在重叠", "expected": f"重叠面积 ≤ {min_area}", "actual": f"{count} 处"})

    def _check_building_boundary(self, gdfs, checks_cfg, qc_rows, issue_frames, ctx):
        """检查建筑越界"""
        if "building" not in gdfs or "boundary" not in gdfs:# 检查建筑/边界
            return
        boundary = gdfs["boundary"].geometry.union_all()
        outside = gdfs["building"][~gdfs["building"].geometry.within(boundary)]
        if len(outside):
            qc_rows.append({"run_id": ctx.run_id, "asset_id": "", "category": "vector", "format": "", "relative_path": "", "rule_id": "BUILDING_OUTSIDE_BOUNDARY", "level": "warn", "message": "建筑落在边界外", "expected": "全部在边界内", "actual": f"{len(outside)} 个"})
            issue_frames.append(outside.assign(rule_id="BUILDING_OUTSIDE_BOUNDARY", level="warn", message="建筑落在边界外"))

    def _check_poi_admin(self, gdfs, checks_cfg, qc_rows, issue_frames, ctx):
        """检查POI是否在行政区面内"""
        if "poi" not in gdfs or "admin" not in gdfs:# 检查POI/行政区
            return
        admin_union = gdfs["admin"].geometry.union_all()# 合并所有行政区面
        outside = gdfs["poi"][~gdfs["poi"].geometry.within(admin_union)]# 筛选出不在行政区面内的POI
        if len(outside):
            qc_rows.append({"run_id": ctx.run_id, "asset_id": "", "category": "vector", "format": "", "relative_path": "", "rule_id": "POI_OUTSIDE_ADMIN", "level": "fail", "message": "POI 不在行政区面内", "expected": "全部在行政区内", "actual": f"{len(outside)} 个"})
            issue_frames.append(outside.assign(rule_id="POI_OUTSIDE_ADMIN", level="fail", message="POI 不在行政区面内"))

    def _check_water_admin(self, gdfs, checks_cfg, qc_rows, issue_frames, ctx):
        """检查水系面是否在行政区面内"""
        if "water" not in gdfs or "admin" not in gdfs:# 检查水系/行政区
            return
        admin_union = gdfs["admin"].geometry.union_all()# 合并所有行政区面
        outside = gdfs["water"][~gdfs["water"].geometry.within(admin_union)]# 筛选出不在行政区面内的水系面
        if len(outside):
            qc_rows.append({"run_id": ctx.run_id, "asset_id": "", "category": "vector", "format": "", "relative_path": "", "rule_id": "WATER_OUTSIDE_ADMIN", "level": "warn", "message": "水系面超出行政区范围", "expected": "全部在行政区内", "actual": f"{len(outside)} 个"})
            issue_frames.append(outside.assign(rule_id="WATER_OUTSIDE_ADMIN", level="warn", message="水系面超出行政区范围"))

    def _qc_row(self, run_id, asset, rule_id, level, message, expected, actual):
        return {
            "run_id": run_id,
            "asset_id": asset.get("asset_id", "") if asset else "",
            "category": "vector",
            "format": asset.get("format", "") if asset else "",
            "relative_path": asset.get("relative_path", "") if asset else "",
            "rule_id": rule_id,
            "level": level,
            "message": message,
            "expected": expected,
            "actual": actual,
        }
